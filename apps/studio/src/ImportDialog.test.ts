import { describe, expect, it } from 'vitest';
import { csvColumns, guessMapping } from './ImportDialog';

describe('import column mapping', () => {
  it('reads header names as the service does: byte-order mark and padding removed, quotes as in CSV', () => {
    expect(csvColumns('\uFEFF Date ,"Open",High,Low,Close,Volume')).toEqual(['Date', 'Open', 'High', 'Low', 'Close', 'Volume']);
    // Python's csv module: a quoted field may hold commas and "" quotes; a quote after a space is literal.
    expect(csvColumns('Date,"Close, settle","Say ""hi""", "Open"')).toEqual(['Date', 'Close, settle', 'Say "hi"', '"Open"']);
  });

  it('maps fields to columns of the same name, ignoring case, and leaves others to the user', () => {
    expect(guessMapping(['DATE', 'open', 'High', 'Low', 'Settle'])).toEqual({ date: 'DATE', open: 'open', high: 'High', low: 'Low', close: '' });
    expect(guessMapping([])).toEqual({ date: '', open: '', high: '', low: '', close: '' });
  });
});
