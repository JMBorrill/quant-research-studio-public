"""The built studio in Chromium against a running service, driven as a user would (Playwright).
Run with `uv run pytest -m e2e` after `npm run build` in apps/studio; CI runs this in its e2e job."""

import io
import json
import re
import socket
import sys
import threading
import time
import zipfile
from pathlib import Path

import httpx
import pytest
import uvicorn
from playwright.sync_api import Browser, Page, expect, sync_playwright

from conftest import EXTENSION_DIR, run_bundle, wait
from qrs import datasets, templates
from qrs.api import STUDIO_DIST, create_app
from qrs.comparison import effective_params
from qrs.contracts import Fill, StrategyDocument
from test_exploratory import news_with_retrospective_data

sys.path.insert(0, str(EXTENSION_DIR))
import qrs_roc  # noqa: E402,F401  the example extension, as QRS_OPERATOR_MODULES would load it

pytestmark = pytest.mark.e2e


@pytest.fixture
def studio(tmp_path):
    """The service with the synthetic dataset on a free loopback port: its URL, an API client and
    its data directory."""
    assert STUDIO_DIST.is_dir(), "Build the studio first: (cd apps/studio && npm ci && npm run build)"
    datasets.create_synthetic(tmp_path)
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(create_app(tmp_path), host="127.0.0.1", port=port, log_level="warning"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.monotonic() + 30
    while not server.started:
        assert thread.is_alive() and time.monotonic() < deadline, "the service did not start"
        time.sleep(0.05)
    url = f"http://127.0.0.1:{port}"
    with httpx.Client(base_url=url) as api:
        yield url, api, tmp_path
    server.should_exit = True
    thread.join()


@pytest.fixture
def browser():
    """A fresh Chromium for each test, closed with every page it opened."""
    with sync_playwright() as playwright:
        chromium = playwright.chromium.launch()
        yield chromium
        chromium.close()


def open_page(browser: Browser, url: str, strategy_id: str | None = None) -> Page:
    """A new browser context (its own storage) showing the studio, or the strategy once loaded."""
    page = browser.new_context(viewport={"width": 1800, "height": 1100}, accept_downloads=True).new_page()
    page.goto(f"{url}/#{strategy_id}" if strategy_id else url)
    expect(page.get_by_label("Strategy name") if strategy_id else page.get_by_role("button", name="New strategy…")).to_be_visible()
    return page


def create_strategy(api: httpx.Client, document: dict | None = None, dataset_id: str | None = None) -> dict:
    body = {"document": document} if document else {"template_id": "ema_crossover", "dataset_id": dataset_id or api.get("/api/datasets").json()[0]["id"]}
    response = api.post("/api/strategies", json=body)
    assert response.status_code == 200, response.text
    return response.json()


def saved(api: httpx.Client, strategy_id: str, check, timeout: float = 15) -> dict:
    """The saved document, once `check` holds for it: autosave writes shortly after each edit."""
    deadline = time.monotonic() + timeout
    while True:
        document = api.get(f"/api/strategies/{strategy_id}").json()["document"]
        if check(document) or time.monotonic() > deadline:
            assert check(document), document
            return document
        time.sleep(0.1)


def node(page: Page, node_id: str):
    return page.locator(f'.react-flow__node[data-id="{node_id}"]')


def connect(page: Page, source: tuple[str, str], target: tuple[str, str]) -> None:
    """Drag from an output handle to an input handle, as a user connects ports."""
    output = node(page, source[0]).locator(f'.react-flow__handle.source[data-handleid="{source[1]}"]')
    output.drag_to(node(page, target[0]).locator(f'.react-flow__handle.target[data-handleid="{target[1]}"]'))


# How a user draws each template from an empty canvas: where each operator is dropped, as fractions of
# the canvas, in the order added (the editor names the nodes after their operators in that order); the
# connections; and the parameters changed from their defaults, by form label.
BOLLINGER_RSI = (
    [
        ("qrs.dataset@1", 0.07, 0.45),
        ("qrs.column@1", 0.25, 0.15),
        ("qrs.bollinger@1", 0.25, 0.45),
        ("qrs.rsi@1", 0.25, 0.8),
        ("qrs.compare@1", 0.45, 0.1),
        ("qrs.threshold@1", 0.45, 0.32),
        ("qrs.compare@1", 0.45, 0.55),
        ("qrs.threshold@1", 0.45, 0.8),
        ("qrs.all@1", 0.65, 0.2),
        ("qrs.any@1", 0.65, 0.65),
        ("qrs.long_flat@1", 0.82, 0.3),
        ("qrs.backtest@1", 0.82, 0.7),
    ],
    [
        (("dataset_1", "bars"), ("column_1", "bars")),
        (("dataset_1", "bars"), ("bollinger_1", "bars")),
        (("dataset_1", "bars"), ("rsi_1", "bars")),
        (("dataset_1", "bars"), ("backtest_1", "bars")),
        (("column_1", "series"), ("compare_1", "a")),  # close below the lower band
        (("bollinger_1", "lower"), ("compare_1", "b")),
        (("rsi_1", "series"), ("threshold_1", "a")),  # RSI below 30
        (("compare_1", "signal"), ("all_1", "a")),
        (("threshold_1", "signal"), ("all_1", "b")),
        (("column_1", "series"), ("compare_2", "a")),  # close at or above the middle band
        (("bollinger_1", "middle"), ("compare_2", "b")),
        (("rsi_1", "series"), ("threshold_2", "a")),  # RSI above 50
        (("compare_2", "signal"), ("any_1", "a")),
        (("threshold_2", "signal"), ("any_1", "b")),
        (("all_1", "signal"), ("long_flat_1", "entry")),
        (("any_1", "signal"), ("long_flat_1", "exit")),
        (("long_flat_1", "positions"), ("backtest_1", "positions")),
    ],
    {
        "compare_1": {"Comparison": "<"},
        "threshold_1": {"Comparison": "<", "Value": "30"},
        "compare_2": {"Comparison": ">="},
        "threshold_2": {"Comparison": ">", "Value": "50"},
    },
)


def crossover(average: str) -> tuple[list, list, dict]:
    """Long when the fast average (period 20, the default) crosses above the slow one (50), flat when it
    crosses below."""
    fast, slow = f"{average}_1", f"{average}_2"
    layout = [
        ("qrs.dataset@1", 0.07, 0.45),
        (f"qrs.{average}@1", 0.3, 0.2),
        (f"qrs.{average}@1", 0.3, 0.7),
        ("qrs.cross@1", 0.55, 0.2),
        ("qrs.cross@1", 0.55, 0.7),
        ("qrs.long_flat@1", 0.8, 0.3),
        ("qrs.backtest@1", 0.8, 0.7),
    ]
    wires = [
        (("dataset_1", "bars"), (fast, "bars")),
        (("dataset_1", "bars"), (slow, "bars")),
        (("dataset_1", "bars"), ("backtest_1", "bars")),
        ((fast, "series"), ("cross_1", "a")),  # fast crosses above slow
        ((slow, "series"), ("cross_1", "b")),
        ((fast, "series"), ("cross_2", "a")),  # fast crosses below slow
        ((slow, "series"), ("cross_2", "b")),
        (("cross_1", "signal"), ("long_flat_1", "entry")),
        (("cross_2", "signal"), ("long_flat_1", "exit")),
        (("long_flat_1", "positions"), ("backtest_1", "positions")),
    ]
    return layout, wires, {slow: {"Period": "50"}, "cross_2": {"Direction": "below"}}


DRAWINGS = {"bollinger_rsi": BOLLINGER_RSI, "ema_crossover": crossover("ema"), "sma_crossover": crossover("sma")}
# The backtest's effective warm-up, worked by hand. Bollinger(20) warms up for 19 sessions and RSI(14)
# for 14, so everything downstream waits 19. A crossover waits 49 sessions for its slow average, then
# one more to see a cross.
WARM_UPS = {"bollinger_rsi": 19, "ema_crossover": 50, "sma_crossover": 50}


def operators_and_params(document: dict) -> list[tuple]:
    """A strategy's nodes as operators with their parameters, defaults filled in, whatever the node IDs."""
    nodes = StrategyDocument.model_validate(document).nodes
    return sorted((n.operator, n.version, json.dumps(effective_params(n), sort_keys=True)) for n in nodes)


def import_fixture(page: Page, template_id: str) -> None:
    """Import a template's fixture CSV through the import dialog, as its declaration describes."""
    declaration = templates.load(template_id).fixture.declaration
    instrument = declaration.instrument
    page.get_by_role("button", name="Import CSV…").click()
    dialog = page.get_by_role("dialog", name="Import CSV")
    dialog.get_by_label("File", exact=False).first.set_input_files(templates.TEMPLATE_DIR / f"{template_id}.csv")
    dialog.get_by_label("Dataset name").fill(declaration.name)
    for label, value in [
        ("Symbol", instrument.symbol),
        ("Multiplier", str(instrument.multiplier)),
        ("Tick size", str(instrument.tick_size)),
        ("Initial margin", str(instrument.margin_init)),
        ("Maintenance margin", str(instrument.margin_maint)),
        ("Expiry", instrument.expiry.isoformat()),
        ("Source", declaration.source),
    ]:
        dialog.get_by_label(label).fill(value)
    dialog.get_by_label("Currency", exact=True).fill(instrument.currency)
    dialog.get_by_label("Evidence class").select_option(declaration.evidence_class)
    dialog.get_by_label("Exports may include the data").set_checked(declaration.redistributable)
    dialog.get_by_role("button", name="Import").click()
    expect(page.get_by_role("status").filter(has_text=f"Imported {declaration.name}")).to_be_visible()


@pytest.mark.parametrize("template_id", DRAWINGS)
def test_draw_each_template_from_an_empty_canvas_then_save_run_and_export(studio, browser, tmp_path, template_id):
    url, api, _ = studio
    layout, wires, params = DRAWINGS[template_id]
    page = open_page(browser, url)
    import_fixture(page, template_id)

    page.get_by_role("button", name="New strategy…").click()
    dialog = page.get_by_role("dialog", name="New strategy")
    dialog.get_by_label("Empty canvas").check()
    dialog.get_by_role("button", name="Create").click()
    expect(page.get_by_role("button", name="New strategy…")).to_be_focused()  # focus returns to the opener
    canvas = page.locator(".canvas__flow")
    expect(canvas).to_be_visible()
    box = canvas.bounding_box()
    for key, x, y in layout:
        page.locator(".palette__item", has_text=key).drag_to(canvas, target_position={"x": box["width"] * x, "y": box["height"] * y})
    for source, target in wires:
        connect(page, source, target)

    node(page, "dataset_1").click()
    fixture_name = templates.load(template_id).fixture.declaration.name
    dataset_id = next(d["id"] for d in api.get("/api/datasets").json() if d["name"] == fixture_name)
    page.get_by_label("Dataset Id").select_option(dataset_id)
    for node_id, fields in params.items():
        node(page, node_id).click()
        for label, value in fields.items():
            field = page.get_by_label(label, exact=False).first
            field.select_option(value) if field.evaluate("e => e.tagName") == "SELECT" else field.fill(value)
    page.locator(".react-flow__pane").click(position={"x": 5, "y": 5})
    expect(page.get_by_text(re.compile(r"^Valid\."))).to_be_visible()
    template = templates.instantiate(template_id, dataset_id)
    expect(node(page, "backtest_1").locator(".node__warmup")).to_have_text(f"warm-up {WARM_UPS[template_id]}")
    strategy_id = page.url.split("#")[1]
    expected = operators_and_params(template.model_dump(mode="json"))
    saved(api, strategy_id, lambda d: len(d["edges"]) == len(template.edges) and operators_and_params(d) == expected)

    page.get_by_role("button", name="Run", exact=True).click()
    expect(page.locator(".run .status").first).to_have_text("succeeded", timeout=90_000)
    run = api.get("/api/runs", params={"strategy_id": strategy_id}).json()[0]
    result = api.get(f"/api/runs/{run['id']}/result").json()
    fills = [Fill.model_validate(f) for f in result["results"]["backtest_1"]["fills"]]
    assert fills == templates.load(template_id).fixture.fills  # the drawn strategy is the template's
    expect(page.locator(".run__detail tbody tr")).to_have_count(len(fills))

    with page.expect_download() as download:
        page.get_by_role("button", name="Export Python").click()
    exported = run_bundle(Path(download.value.path()).read_bytes(), tmp_path)
    app = result["record"]
    assert (exported["code_sha256"], exported["result_sha256"], exported["outputs"]) == (app["code_sha256"], app["result_sha256"], app["outputs"])


def test_a_stale_save_is_refused_and_its_edits_can_be_recovered(studio, browser):
    url, api, _ = studio
    strategy = create_strategy(api)
    first, second = open_page(browser, url, strategy["id"]), open_page(browser, url, strategy["id"])

    first.get_by_label("Strategy name").fill("Saved first")
    saved(api, strategy["id"], lambda d: d["name"] == "Saved first")
    second.get_by_label("Strategy name").fill("Saved second")
    expect(second.locator(".save")).to_contain_text("saved elsewhere")
    assert saved(api, strategy["id"], lambda d: True)["name"] == "Saved first"  # the stale save was refused

    second.get_by_role("button", name="Reload").click()
    expect(second.get_by_label("Strategy name")).to_have_value("Saved first")
    notice = second.get_by_role("alert").filter(has_text="could not be saved")
    expect(notice).to_contain_text("restoring replaces those changes")
    notice.get_by_role("button", name="Restore").click()
    expect(second.get_by_label("Strategy name")).to_have_value("Saved second")
    saved(api, strategy["id"], lambda d: d["name"] == "Saved second")


def test_a_run_started_before_the_browser_closes_is_finished_when_the_studio_reopens(studio, browser):
    url, api, home = studio
    # A longer dataset gives the run more time; the check below confirms it was unfinished.
    strategy = create_strategy(api, dataset_id=datasets.create_synthetic(home, seed=7, sessions=1700))
    page = open_page(browser, url, strategy["id"])
    page.get_by_role("button", name="Run", exact=True).click()
    expect(page.locator(".run .status").first).to_have_text(re.compile("queued|running"))
    page.context.close()
    assert api.get("/api/runs", params={"strategy_id": strategy["id"]}).json()[0]["status"] in ("queued", "running")

    reopened = open_page(browser, url, strategy["id"])
    reopened.locator(".run").first.click()
    expect(reopened.locator(".run .status").first).to_have_text("succeeded", timeout=90_000)
    expect(reopened.get_by_text("Net P&L")).to_be_visible()


def unconnected(api: httpx.Client) -> dict:
    """A strategy with a Dataset, an EMA and a Cross node, laid out apart and not yet connected."""
    nodes = [
        {"id": "data", "operator": "qrs.dataset", "version": 1, "params": {}},
        {"id": "average", "operator": "qrs.ema", "version": 1, "params": {}},
        {"id": "crossing", "operator": "qrs.cross", "version": 1, "params": {}},
    ]
    layout = {"data": {"x": 0, "y": 100}, "average": {"x": 320, "y": 0}, "crossing": {"x": 320, "y": 220}}
    return create_strategy(api, {"name": "Connections", "nodes": nodes, "edges": [], "layout": layout})


def test_a_connection_between_mismatched_types_is_refused(studio, browser):
    url, api, _ = studio
    strategy = unconnected(api)
    page = open_page(browser, url, strategy["id"])

    connect(page, ("data", "bars"), ("crossing", "a"))  # bars into a series input
    connect(page, ("data", "bars"), ("average", "bars"))  # matching types, for contrast
    expect(page.locator(".react-flow__edge")).to_have_count(1)
    document = saved(api, strategy["id"], lambda d: d["edges"])
    assert document["edges"] == [{"source": {"node": "data", "port": "bars"}, "target": {"node": "average", "port": "bars"}}]


def test_edits_keep_node_measurements_so_connections_are_not_dropped(studio, browser):
    """The canvas re-derives its nodes after each edit and again when the edit's check returns. If
    React Flow had to measure them again, their handles would accept no connection meanwhile."""
    url, api, _ = studio
    page = open_page(browser, url, create_strategy(api)["id"])
    expect(page.get_by_text(re.compile(r"^Valid\."))).to_be_visible()
    page.evaluate(
        """() => {
          window.remeasured = 0;  // React Flow hides a node until it has been measured
          new MutationObserver((records) => {
            for (const r of records) if (r.target.classList.contains('react-flow__node') && r.target.style.visibility === 'hidden') window.remeasured++;
          }).observe(document.querySelector('.react-flow'), { attributes: true, subtree: true, attributeFilter: ['style'] });
        }"""
    )
    page.get_by_label("Strategy name").fill("Renamed")
    saved(api, page.url.split("#")[1], lambda d: d["name"] == "Renamed")
    expect(page.locator(".node__warmup").first).to_be_visible()  # the edit's check has returned
    assert page.evaluate("window.remeasured") == 0


def test_undo_and_redo_from_the_keyboard_save_each_step(studio, browser):
    url, api, _ = studio
    strategy = unconnected(api)
    page = open_page(browser, url, strategy["id"])
    connect(page, ("data", "bars"), ("average", "bars"))
    expect(page.locator(".react-flow__edge")).to_have_count(1)
    saved(api, strategy["id"], lambda d: len(d["edges"]) == 1)

    page.locator(".react-flow__pane").click(position={"x": 5, "y": 5})
    page.keyboard.press("Control+z")
    expect(page.locator(".react-flow__edge")).to_have_count(0)
    saved(api, strategy["id"], lambda d: d["edges"] == [])
    page.keyboard.press("Control+Shift+z")
    expect(page.locator(".react-flow__edge")).to_have_count(1)
    saved(api, strategy["id"], lambda d: len(d["edges"]) == 1)

    # A drag (by the node's title, in steps, as a hand moves) is one step.
    title = node(page, "crossing").locator(".node__head").bounding_box()
    page.mouse.move(title["x"] + 20, title["y"] + 8)
    page.mouse.down()
    for i in range(1, 11):
        page.mouse.move(title["x"] + 20 + 30 * i, title["y"] + 8 + 20 * i)
    page.mouse.up()
    saved(api, strategy["id"], lambda d: d["layout"]["crossing"] != {"x": 320, "y": 220})
    page.keyboard.press("Control+z")
    saved(api, strategy["id"], lambda d: d["layout"]["crossing"] == {"x": 320, "y": 220})

    # Deleting a connected node removes its connection in the same step.
    node(page, "average").click()
    page.keyboard.press("Delete")
    saved(api, strategy["id"], lambda d: len(d["nodes"]) == 2 and d["edges"] == [])
    page.keyboard.press("Control+z")
    saved(api, strategy["id"], lambda d: len(d["nodes"]) == 3 and len(d["edges"]) == 1)


def finished_run(api: httpx.Client, commission: float) -> dict:
    """An EMA crossover run on the synthetic data, with the given commission per contract."""
    strategy = create_strategy(api)
    document = strategy["document"]
    next(n for n in document["nodes"] if n["id"] == "backtest")["params"]["commission_per_contract"] = commission
    revision = api.put(f"/api/strategies/{strategy['id']}", json={"revision": strategy["revision"], "document": document}).json()["revision"]
    run = wait(api, api.post(f"/api/strategies/{strategy['id']}/runs", json={"revision": revision}).json()["id"])
    assert run["status"] == "succeeded", run["error"]
    return run


def test_compare_runs_then_rerun_one(studio, browser):
    url, api, _ = studio
    usual, pricier = finished_run(api, 2.5), finished_run(api, 5.0)
    page = open_page(browser, url, usual["strategy_id"])

    page.get_by_role("button", name="Compare runs…").click()
    dialog = page.get_by_role("dialog", name="Compare runs")
    compare = dialog.get_by_role("button", name="Compare", exact=True)
    dialog.get_by_label(re.compile(usual["id"])).check()
    expect(compare).to_be_disabled()  # at least two runs
    dialog.get_by_label(re.compile(pricier["id"])).check()
    compare.click()
    expect(dialog.get_by_role("alert")).to_contain_text("Not like for like")
    expect(dialog.get_by_role("alert")).to_contain_text("commission_per_contract")
    expect(dialog.get_by_text(re.compile(r"^Common window: .* sessions\."))).to_be_visible()
    expect(dialog.locator(".chart polyline")).to_have_count(2)
    expect(dialog.get_by_role("row", name=re.compile("^Net P&L"))).to_be_visible()
    expect(dialog.get_by_role("row", name=re.compile("^Sessions excluded 50 50"))).to_be_visible()
    expect(dialog.get_by_role("row", name=re.compile("^backtest commission_per_contract 2.5 5"))).to_be_visible()
    dialog.get_by_role("button", name="Choose other runs").click()
    dialog.get_by_role("button", name="Close").click()

    page.locator(".run").first.click()
    page.get_by_role("button", name="Re-run").click()
    expect(page.locator(".run").first).to_contain_text(f"re-run of {usual['id']}")
    expect(page.locator(".run .status").first).to_have_text("succeeded", timeout=90_000)
    rerun = api.get("/api/runs", params={"strategy_id": usual["strategy_id"]}).json()[0]
    assert (rerun["predecessor"], rerun["result_sha256"]) == (usual["id"], usual["result_sha256"])


def test_a_custom_table_operator_appears_in_the_palette_then_runs_and_exports(studio, browser, tmp_path, monkeypatch):
    monkeypatch.setenv("PYTHONPATH", str(EXTENSION_DIR))  # workers import the module a strategy names
    url, api, _ = studio
    strategy = create_strategy(api)
    page = open_page(browser, url, strategy["id"])
    item = page.locator(".palette__item", has_text="ext.candle@1")
    expect(page.locator(".palette__family", has=item).locator("h3")).to_have_text("features")
    canvas = page.locator(".canvas__flow")
    box = canvas.bounding_box()
    item.drag_to(canvas, target_position={"x": box["width"] * 0.5, "y": box["height"] * 0.85})
    connect(page, ("bars", "bars"), ("candle_1", "bars"))
    expect(page.locator(".react-flow__edge")).to_have_count(11)
    page.locator(".react-flow__pane").click(position={"x": 5, "y": 5})
    expect(page.get_by_text(re.compile(r"^Valid\."))).to_be_visible()
    saved(api, strategy["id"], lambda d: len(d["edges"]) == 11 and any(n["operator"] == "ext.candle" for n in d["nodes"]))

    page.get_by_role("button", name="Run", exact=True).click()
    expect(page.locator(".run .status").first).to_have_text("succeeded", timeout=90_000)
    run = api.get("/api/runs", params={"strategy_id": strategy["id"]}).json()[0]
    record = api.get(f"/api/runs/{run['id']}/result").json()["record"]
    assert record["operators"]["ext.candle@1"]["module"] == "qrs_roc"

    with page.expect_download() as download:
        page.get_by_role("button", name="Export Python").click()
    bundle = Path(download.value.path()).read_bytes()
    assert "qrs_roc.py" in zipfile.ZipFile(io.BytesIO(bundle)).namelist()
    assert run_bundle(bundle, tmp_path)["result_sha256"] == record["result_sha256"]


def test_experiments_are_ranked_compared_cancelled_and_their_holdout_evaluated_once(studio, browser):
    url, api, data_dir = studio
    strategy = create_strategy(api)
    holdout = datasets.load(data_dir, api.get("/api/datasets").json()[0]["id"]).frame.index[400].date().isoformat()
    page = open_page(browser, url, strategy["id"])

    def new_experiment(values: str) -> None:
        dialog.get_by_role("button", name="New experiment…").click()
        dialog.get_by_label("Parameter 1", exact=True).select_option("fast.period")
        dialog.get_by_label("Values 1").fill(values)
        dialog.get_by_label("Holdout start").fill(holdout)
        dialog.get_by_role("button", name="Run experiment").click()

    def hold(pattern: str) -> list:
        """Hold the next POST to `pattern` until the test continues it, as a slow service would."""
        held = []
        page.route(pattern, lambda route: held.append(route) if route.request.method == "POST" else route.continue_())
        return held

    def release(held: list, pattern: str) -> None:
        while not held:
            page.wait_for_timeout(50)
        held[0].continue_()
        page.unroute(pattern)

    page.get_by_role("button", name="Experiments…").click()
    dialog = page.get_by_role("dialog", name="Experiments")
    new_experiment("10, 20")
    ranking = dialog.get_by_role("table", name="Ranking")
    expect(ranking.locator("tbody tr")).to_have_count(2, timeout=90_000)
    expect(dialog.locator(".status")).to_have_text("finished")
    listed = api.get("/api/experiments", params={"strategy_id": strategy["id"]}).json()[0]
    experiment = api.get(f"/api/experiments/{listed['id']}").json()
    ranked = [run["trial"] for run in experiment["ranking"]["runs"]]
    expect(ranking.locator("tbody tr td:nth-child(3)")).to_have_text([str(t) for t in ranked])  # best first, as the service ranks
    expect(ranking.locator("tbody tr td:nth-child(6)")).to_have_text(["synthetic", "synthetic"])  # each trial's evidence class

    dialog.get_by_label("Compare trial 1").check()
    dialog.get_by_label("Compare trial 2").check()
    dialog.get_by_role("button", name="Compare selected").click()
    expect(dialog.get_by_text(re.compile(r"^Common window:"))).to_be_visible()
    dialog.get_by_role("button", name="Choose other runs").click()

    dialog.get_by_role("button", name="Evaluate holdout…").click()
    expect(dialog.get_by_role("group", name="Confirm holdout evaluation")).to_contain_text(f"Evaluate trial {ranked[0]} on the holdout")
    held = hold("**/holdout")
    dialog.get_by_role("button", name="Evaluate holdout", exact=True).click()
    expect(dialog.get_by_role("button", name="Evaluate holdout…")).to_be_disabled()  # no second evaluation while the first is pending
    release(held, "**/holdout")
    evaluations = dialog.get_by_role("table", name="Holdout evaluations").locator("tbody tr")
    expect(evaluations).to_have_count(1)
    expect(evaluations.first).to_contain_text("First evaluation")
    expect(evaluations.first).to_contain_text("succeeded", timeout=90_000)
    expect(evaluations.first).to_contain_text(f"{holdout} to ")
    recorded = api.get(f"/api/experiments/{experiment['id']}").json()["holdout"]
    assert [(e["trial"], e["repeat"]) for e in recorded] == [(ranked[0], False)]

    dialog.get_by_role("button", name="All experiments").click()
    held = hold("**/api/experiments")
    new_experiment(", ".join(str(p) for p in range(5, 35)))  # 30 trials, cancelled while they run
    # Leaving while it is created would open another experiment's view when it is.
    expect(dialog.get_by_role("button", name="Back")).to_be_disabled()
    release(held, "**/api/experiments")
    # While trials still run the ranking can change, so the holdout is not offered.
    expect(dialog.get_by_role("table", name="Ranking").locator("tbody tr").first).to_be_visible(timeout=90_000)
    expect(dialog.locator(".status")).to_have_text("running")
    expect(dialog.get_by_role("button", name="Evaluate holdout…")).to_have_count(0)
    dialog.get_by_role("button", name="Cancel experiment").click()
    expect(dialog.locator(".status")).to_have_text("cancelled")
    # The view follows the running trial until it has stopped, though the experiment is cancelled.
    statuses = dialog.get_by_role("table", name="Trials").locator("tbody tr td:nth-child(3)")
    expect(statuses.filter(has_text=re.compile(r"^(queued|running)"))).to_have_count(0, timeout=30_000)
    second = next(e for e in api.get("/api/experiments", params={"strategy_id": strategy["id"]}).json() if e["id"] != experiment["id"])
    assert second["status"] == "cancelled" and second["counts"].get("cancelled", 0) >= 20
    dialog.get_by_role("button", name="All experiments").click()
    expect(dialog.get_by_role("row")).to_have_count(3)  # a header and both experiments


def test_the_ridge_template_is_in_the_gallery_and_runs(studio, browser, tmp_path):
    url, api, _ = studio
    page = open_page(browser, url)
    page.get_by_role("button", name="New strategy…").click()
    dialog = page.get_by_role("dialog", name="New strategy")
    dialog.locator(".gallery__item", has_text="Ridge return forecast").locator("input").check()
    expect(dialog.locator(".gallery__item", has_text="Ridge return forecast")).to_contain_text("Chronological training")
    dialog.get_by_role("button", name="Create").click()
    expect(page.get_by_text(re.compile(r"^Valid\."))).to_be_visible()
    page.get_by_role("button", name="Run", exact=True).click()
    expect(page.locator(".run .status").first).to_have_text("succeeded", timeout=90_000)
    expect(page.locator(".run__detail")).to_contain_text("Evidence: synthetic")
    strategy_id = page.url.split("#")[1]
    run = api.get("/api/runs", params={"strategy_id": strategy_id}).json()[0]
    record = api.get(f"/api/runs/{run['id']}/result").json()["record"]
    assert "models/fit.json" in record["outputs"]

    # The bundle exported from the studio reproduces the studio's run, model included.
    with page.expect_download() as download:
        page.get_by_role("button", name="Export Python").click()
    exported = run_bundle(Path(download.value.path()).read_bytes(), tmp_path)
    assert (exported["code_sha256"], exported["result_sha256"], exported["outputs"]) == (record["code_sha256"], record["result_sha256"], record["outputs"])


def test_exploratory_features_are_labelled_refused_from_verified_experiments_and_their_holdouts_kept_apart(studio, browser):
    url, api, data_dir = studio
    chosen = news_with_retrospective_data(data_dir)  # bars and stories declared retrospective, so the features are exploratory
    page = open_page(browser, url)
    page.get_by_role("button", name="New strategy…").click()
    dialog = page.get_by_role("dialog", name="New strategy")
    dialog.locator(".gallery__item", has_text="News and technical trend").locator("input").check()
    dialog.get_by_label("Bars", exact=True).select_option(chosen[datasets.BARS])
    dialog.get_by_label("Observations or features", exact=True).select_option(chosen[datasets.OBSERVATIONS])
    dialog.get_by_role("button", name="Create").click()
    expect(page.get_by_text(re.compile(r"^Valid\."))).to_be_visible()
    node(page, "sentiment").click()
    expect(page.locator(".provenance")).to_have_text(
        "Extracted by fixture fixture from 15 responses: 13 of 15 rows with values; 15 captured after they could be used."
    )
    page.get_by_role("button", name="Run", exact=True).click()
    expect(page.locator(".run .status").first).to_have_text("succeeded", timeout=90_000)
    expect(page.locator(".run__detail")).to_contain_text("Evidence: exploratory")

    page.get_by_role("button", name="Experiments…").click()
    experiments = page.get_by_role("dialog", name="Experiments")

    def new_experiment(values: str, exploratory: bool) -> None:
        experiments.get_by_role("button", name="New experiment…").click()
        experiments.get_by_label("Parameter 1", exact=True).select_option("sentiment.window_days")
        experiments.get_by_label("Values 1").fill(values)
        experiments.get_by_label("Holdout start").fill("2024-07-01")
        if exploratory:
            experiments.get_by_label("Exploratory analysis").check()
        experiments.get_by_role("button", name="Run experiment").click()

    def evaluate_holdout() -> object:
        expect(experiments.locator(".status")).to_have_text("finished", timeout=90_000)
        experiments.get_by_role("button", name="Evaluate holdout…").click()
        experiments.get_by_role("button", name="Evaluate holdout", exact=True).click()
        evaluation = experiments.get_by_role("table", name="Holdout evaluations").locator("tbody tr").first
        expect(evaluation).to_contain_text("succeeded", timeout=90_000)
        expect(evaluation).to_contain_text("Exploratory")
        return evaluation

    # Verified (the default): refused, since the strategy reads exploratory data.
    new_experiment("2, 3", exploratory=False)
    expect(experiments.get_by_role("alert")).to_contain_text("reads exploratory data (News sentiment (retrospective)), so its results cannot be verified")
    experiments.get_by_label("Exploratory analysis").check()
    experiments.get_by_role("button", name="Run experiment").click()
    expect(experiments.get_by_role("note")).to_contain_text("Exploratory analysis")
    expect(evaluate_holdout()).to_contain_text("First evaluation")

    # Another experiment on the same holdout: a repeat, listed with the first, apart from verified ones.
    experiments.get_by_role("button", name="All experiments").click()
    new_experiment("3, 4", exploratory=True)
    expect(evaluate_holdout()).to_contain_text("Repeat: 1 exploratory evaluation of this holdout came before")
    expect(experiments.get_by_role("table", name="Exploratory evaluations of this holdout").locator("tbody tr")).to_have_count(2)
    expect(experiments.get_by_role("table", name="Verified evaluations of this holdout")).to_have_count(0)


def test_research_create_a_project_register_a_family_run_an_analysis_and_view_a_report(studio, browser):
    """Stage 5 workstream 9: the research views, offline, as a user would drive them."""
    url, api, home = studio
    dataset = api.get("/api/datasets").json()[0]
    page = open_page(browser, url)
    page.get_by_role("button", name="Research…").click()
    dialog = page.get_by_role("dialog", name="Research")

    dialog.get_by_role("button", name="New project…").click()
    dialog.get_by_label("Project name").fill("Synthetic drift")
    dialog.get_by_label("Research question").fill("Do returns drift upward?")
    dialog.get_by_label(dataset["name"]).check()
    dialog.get_by_label("Confirmation start").fill("2025-06-02")
    dialog.get_by_role("button", name="Create project").click()
    expect(dialog.get_by_role("heading", name="Synthetic drift")).to_be_visible()
    expect(dialog.get_by_text("never read")).to_be_visible()

    dialog.get_by_role("button", name="Register a family…").click()
    dialog.get_by_label("Family name").fill("Drift")
    dialog.get_by_label("Statement").fill("Mean returns are positive.")
    dialog.get_by_label("Method").select_option(label="Mean test")
    dialog.get_by_label("values: dataset").select_option(dataset["id"])
    dialog.get_by_role("button", name="Add hypothesis").click()
    dialog.get_by_role("button", name="Review and register…").click()
    expect(dialog.get_by_text("this cannot be changed")).to_be_visible()
    dialog.get_by_role("button", name="Register family").click()
    expect(dialog.get_by_text("Drift · confirmatory")).to_be_visible()
    project_id = api.get("/api/projects").json()[0]["id"]
    # Evaluation is binding, so it is confirmed first; the outcome appears once the analysis finishes.
    dialog.get_by_role("button", name="Evaluate h1").click()
    expect(dialog.get_by_text("It reads the test period, once")).to_be_visible()
    dialog.get_by_role("button", name="Confirm evaluation").click()
    expect(dialog.get_by_text(re.compile(r"h1: evaluated, p = "))).to_be_visible(timeout=60_000)

    dialog.get_by_role("tab", name="Analyses").click()
    dialog.get_by_role("button", name="New analysis…").click()
    dialog.get_by_label("Method").select_option(label="Parameter sensitivity")  # a list parameter with no default
    dialog.get_by_role("textbox", name="Axes").fill("fast.period, slow.period")
    expect(dialog.get_by_role("textbox", name="Axes")).to_have_value("fast.period, slow.period")
    dialog.get_by_label("Method").select_option(label="Distribution")
    dialog.get_by_label("values: dataset").select_option(dataset["id"])
    dialog.get_by_label("Analysis name").fill("Return histogram")
    dialog.get_by_label("Project").select_option(label="Synthetic drift")
    dialog.get_by_role("button", name="Run analysis").click()
    dialog.get_by_role("button", name="Return histogram · succeeded").click(timeout=60_000)
    expect(dialog.get_by_role("img", name="histogram (aggregates)")).to_be_visible()
    assert dialog.get_by_role("img", name="histogram (aggregates)").evaluate("img => img.naturalWidth") == 640  # the SVG loaded as an image

    dialog.get_by_role("tab", name="Reports").click()
    dialog.get_by_label("Report project").select_option(project_id)
    dialog.get_by_label("Report title").fill("Drift <findings>")
    dialog.get_by_label("Return histogram (qrs.distribution@1)").check()
    dialog.get_by_role("button", name="Build report").click()
    frame = page.frame_locator("iframe[title=Report]")
    expect(frame.get_by_role("heading", name="Drift <findings>")).to_be_visible()
    assert dialog.locator("iframe[title=Report]").get_attribute("sandbox") == ""
    dialog.get_by_role("button", name="Verify").click()
    expect(dialog.get_by_text("Verified against the store.")).to_be_visible()
    # A second report starts unverified.
    dialog.get_by_role("button", name="New report…").click()
    dialog.get_by_label("Report project").select_option(project_id)
    dialog.get_by_label("Report title").fill("Second")
    dialog.get_by_label("Family Drift (confirmatory)").check()
    dialog.get_by_role("button", name="Build report").click()
    expect(frame.get_by_role("heading", name="Second")).to_be_visible()
    expect(dialog.get_by_text("Verified against the store.")).to_have_count(0)
