const {test} = require('node:test');
const assert = require('node:assert/strict');
const {inspect,MAX_INPUT} = require('../web/inspector.js');
const response=(reason,calls)=>JSON.stringify({choices:[{message:{tool_calls:calls},finish_reason:reason}]});
const call=(args)=>({id:'call_example',type:'function',function:{name:'lookup',arguments:args}});
test('reports conflicting stop and tool call without inspecting secrets',()=>{
  const x=inspect(response('stop',[call('{"private":"example"}')]));
  assert.equal(x.kind,'mismatch'); assert.equal(x.calls,1); assert.equal(x.validJson,1);
  assert.equal(JSON.stringify(x).includes('private'),false);
});
test('normal call, truncated output and missing calls stay distinct',()=>{
  assert.equal(inspect(response('tool_calls',[call('{}')])).kind,'calls');
  assert.equal(inspect(response('tool_calls',[call('{')])).kind,'invalid_arguments');
  assert.equal(inspect(response('length',[call('{')])).kind,'length');
  assert.equal(inspect(response('tool_calls',[])).kind,'missing_calls');
  assert.equal(inspect(response('stop',[])).kind,'no_calls');
});
test('counts invalid and missing arguments without inferring schema compliance',()=>{
  const x=inspect(response('tool_calls',[call('{'),{},call('[]')]));
  assert.equal(x.kind,'malformed_calls');
  assert.deepEqual([x.validJson,x.invalidJson,x.missingArgs],[1,1,1]);
});
test('rejects invalid, oversized, streamed and wrong-shape responses',()=>{
  assert.equal(inspect('').kind,'empty');
  assert.equal(inspect('x'.repeat(MAX_INPUT+1)).kind,'too_large');
  assert.equal(inspect('{').kind,'invalid_json');
  assert.equal(inspect(JSON.stringify({choices:[{delta:{},finish_reason:'stop'}]})).kind,'wrong_shape');
  assert.equal(inspect(JSON.stringify({choices:[{message:{tool_calls:'bad'}}]})).kind,'wrong_shape');
  assert.equal(inspect(JSON.stringify({output:[]})).kind,'wrong_shape');
});
test('does not claim success for missing or unknown finish reason',()=>{
  assert.equal(inspect(response(null,[call('{}')])).kind,'other');
  assert.equal(inspect(response('unrecognized',[call('{}')])).kind,'other');
});
