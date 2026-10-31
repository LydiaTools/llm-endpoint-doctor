(function (root, factory) {
  const api = factory();
  if (typeof module === 'object' && module.exports) module.exports = api;
  else root.ResponseInspector = api;
}(typeof globalThis !== 'undefined' ? globalThis : this, function () {
  'use strict';
  const MAX_INPUT = 200000;
  const object = value => value !== null && typeof value === 'object' && !Array.isArray(value);
  function inspect(text) {
    if (typeof text !== 'string' || !text.trim()) return { kind: 'empty' };
    if (text.length > MAX_INPUT) return { kind: 'too_large' };
    let data;
    try { data = JSON.parse(text); } catch (_) { return { kind: 'invalid_json' }; }
    if (!object(data) || !Array.isArray(data.choices) || !object(data.choices[0])) return { kind: 'wrong_shape' };
    const choice = data.choices[0];
    if (!object(choice.message) || ('delta' in choice)) return { kind: 'wrong_shape' };
    const reason = typeof choice.finish_reason === 'string' ? choice.finish_reason : null;
    const raw = choice.message.tool_calls;
    if (raw != null && !Array.isArray(raw)) return { kind: 'wrong_shape' };
    const calls = raw || [];
    let validJson = 0, invalidJson = 0, missingArgs = 0, malformed = 0;
    for (const call of calls) {
      if (!object(call) || call.type !== 'function' || typeof call.id !== 'string' || !call.id ||
          !object(call.function) || typeof call.function.name !== 'string' || !call.function.name) malformed++;
      const args = object(call) && object(call.function) ? call.function.arguments : undefined;
      if (typeof args !== 'string') { missingArgs++; continue; }
      try { JSON.parse(args); validJson++; } catch (_) { invalidJson++; }
    }
    const facts = { reason, calls: calls.length, validJson, invalidJson, missingArgs, choices: data.choices.length };
    let kind = 'other';
    if (reason === 'length') kind = 'length';
    else if (calls.length && malformed) kind = 'malformed_calls';
    else if (reason === 'stop' && calls.length) kind = 'mismatch';
    else if (reason === 'tool_calls' && calls.length && (invalidJson || missingArgs)) kind = 'invalid_arguments';
    else if (reason === 'tool_calls' && calls.length) kind = 'calls';
    else if (reason === 'tool_calls') kind = 'missing_calls';
    else if (reason === 'stop') kind = 'no_calls';
    return { kind, ...facts };
  }
  return { inspect, MAX_INPUT };
}));
