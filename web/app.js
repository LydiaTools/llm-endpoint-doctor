(function () {
  'use strict';
  const words = {
    en: {
      title:'Did the tool call arrive?', intro:'Check a Chat Completions response when your agent stops before using a tool.',
      inputHeading:'Response body', inputLabel:'Paste a non-streaming Chat Completions JSON response', example:'Try an example',
      help:'Paste the JSON body only. Remove keys, headers, and private content first.', inspect:'Check response', clear:'Clear',
      resultHeading:'What the response says', local:'On this device', emptyTitle:'Ready when you are', emptyDetail:'Paste a response or try the example to see the result.',
      reason:'Finish reason', callsLabel:'Tool calls', arguments:'Arguments', scope:'Checks the first choice in a non-streaming Chat Completions JSON response. No API call is made; nothing is uploaded or executed.',
      source:'Source and live endpoint checks', stream:'Inspect a streaming capture', unavailable:'Not available', otherReason:'Other value',
      argumentSummary:(ok,bad,missing)=>`${ok} JSON · ${bad} invalid · ${missing} missing`,
      empty:['Add a response first','Paste a JSON response or choose Try an example.',''],
      too_large:['Response is too large','The limit is 200,000 characters. Use a smaller redacted response body.',''],
      invalid_json:['This is not valid JSON','Check that you copied a complete JSON response body.','Streaming SSE captures need a different inspector.'],
      wrong_shape:['Response shape not recognized','Expected choices[0].message in a non-streaming Chat Completions response.','Responses API objects and streaming chunks need a different inspector.'],
      mismatch:['Tool call present; finish reason is stop','The response includes tool calls but also says stop. A client may treat the turn as finished.','Check how your client handles this combination. No function was executed.'],
      malformed_calls:['Tool-call data needs review','At least one call is missing a function, name, ID, or supported type.','Compare the full response with your provider’s format; no function was executed.'],
      invalid_arguments:['Tool-call arguments need review','At least one argument string is missing or is not valid JSON.','Check the original response before passing arguments to a function.'],
      length:['Output limit reached','The response says length. Tool arguments or other output may be incomplete.','Inspect the original response and output limit before using the result.'],
      calls:['Tool calls are present','The response reports tool_calls and includes one or more calls.','Check each call against the expected schema in your client. This is not an execution or compatibility test.'],
      missing_calls:['Tool calls are missing','The finish reason says tool_calls, but the response has no tool calls in the first choice.','Inspect the complete response and the provider or gateway.'],
      no_calls:['No tool calls in this choice','The response reports stop and contains no tool calls in the first choice.','This can be a normal text answer; it does not establish whether the model supports tools.'],
      other:['Review the finish reason','The response has an absent or different finish reason.','Check the provider and client documentation; this tool does not infer missing information.']
    },
    zh: {
      title:'工具调用返回了吗？', intro:'Agent 还没使用工具就停下时，检查一条 Chat Completions 响应。',
      inputHeading:'响应正文', inputLabel:'粘贴非流式 Chat Completions JSON 响应', example:'试用示例',
      help:'只粘贴 JSON 正文，请先移除密钥、请求头和私人内容。', inspect:'检查响应', clear:'清空',
      resultHeading:'响应里有什么', local:'在本机处理', emptyTitle:'从一条响应开始', emptyDetail:'粘贴响应，或点击示例查看结果。',
      reason:'结束原因', callsLabel:'工具调用', arguments:'参数', scope:'只检查非流式 Chat Completions JSON 的第一项结果。不调用 API，不上传或执行内容。',
      source:'查看源码与在线接口检查', stream:'检查流式响应', unavailable:'未提供', otherReason:'其他值',
      argumentSummary:(ok,bad,missing)=>`${ok} 项是 JSON · ${bad} 项无效 · ${missing} 项缺失`,
      empty:['请先输入响应','粘贴一条 JSON 响应，或点击“试用示例”。',''],
      too_large:['响应过大','上限为 200,000 个字符，请缩小并脱敏后重试。',''],
      invalid_json:['JSON 格式不完整','请检查是否复制了完整的 JSON 响应正文。','流式 SSE 响应请使用流式检查器。'],
      wrong_shape:['无法识别响应结构','需要非流式 Chat Completions 响应中的 choices[0].message。','Responses API 和流式片段需要使用其他检查器。'],
      mismatch:['有工具调用，但结束原因为 stop','响应包含工具调用，同时标记为 stop；客户端可能会将本轮视为结束。','请检查客户端如何处理这种组合。本工具没有执行函数。'],
      malformed_calls:['工具调用数据需要检查','至少一条调用缺少函数、名称、ID，或使用了不支持的类型。','请对照服务商格式核对完整响应，本工具没有执行函数。'],
      invalid_arguments:['工具调用参数需要检查','至少一条参数缺失，或不是有效的 JSON 字符串。','将参数传给函数之前，请核对原始响应。'],
      length:['已达到输出上限','响应标记为 length，工具参数或其他内容可能不完整。','使用结果前，请核对原始响应和输出上限。'],
      calls:['检测到工具调用','响应标记为 tool_calls，且包含一条或多条调用。','请在客户端对照预期 Schema 检查每条调用；本工具不测试执行或完整兼容性。'],
      missing_calls:['缺少工具调用','结束原因是 tool_calls，但第一项结果中没有工具调用。','请核对完整响应，以及服务商或网关的处理方式。'],
      no_calls:['本条结果没有工具调用','结束原因为 stop，且第一项结果中没有工具调用。','这可能是正常的文字回复，不能据此判断模型是否支持工具。'],
      other:['请核对结束原因','响应缺少结束原因，或使用了其他结束原因。','请查阅服务商和客户端文档；本工具不会推断缺失信息。']
    }
  };
  const example = { choices:[{ finish_reason:'stop', message:{ tool_calls:[{ id:'call_example', type:'function', function:{ name:'lookup_status', arguments:'{"item":"example"}' } }] } }] };
  const $ = id => document.getElementById(id);
  const input = $('response');
  let lang = new URLSearchParams(location.search).get('lang') === 'zh' ? 'zh' : 'en';
  let current = null;
  function render() {
    const w = words[lang];
    document.documentElement.lang = lang === 'zh' ? 'zh-CN' : 'en';
    document.querySelectorAll('[data-i18n]').forEach(node => { node.textContent = w[node.dataset.i18n]; });
    $('language').textContent = lang === 'en' ? '中文' : 'English';
    $('language').setAttribute('aria-label', lang === 'en' ? '切换至中文' : 'Switch to English');
    const panel = $('result');
    if (!current) { panel.className = 'result empty'; $('symbol').textContent = '⌁'; $('facts').hidden = true; $('next').hidden = true; return; }
    const entry = w[current.kind];
    $('result-title').textContent = entry[0]; $('result-detail').textContent = entry[1];
    const issue = ['mismatch','length','missing_calls','malformed_calls','invalid_arguments','other'].includes(current.kind);
    const error = ['invalid_json','wrong_shape','too_large','empty'].includes(current.kind);
    panel.className = 'result ' + (error ? 'error' : issue ? 'warning' : '');
    $('symbol').textContent = error ? '!' : issue ? '!' : '✓';
    const hasFacts = Object.prototype.hasOwnProperty.call(current, 'calls');
    $('facts').hidden = !hasFacts;
    if (hasFacts) {
      const known = ['stop','length','tool_calls','function_call','content_filter'];
      $('fact-reason').textContent = !current.reason ? w.unavailable : known.includes(current.reason) ? current.reason : w.otherReason;
      $('fact-calls').textContent = String(current.calls);
      $('fact-arguments').textContent = current.calls ? w.argumentSummary(current.validJson,current.invalidJson,current.missingArgs) : '—';
    }
    $('next').textContent = entry[2]; $('next').hidden = !entry[2];
  }
  function check() { current = ResponseInspector.inspect(input.value); render(); }
  $('inspect').addEventListener('click', check);
  $('example').addEventListener('click', () => { input.value = JSON.stringify(example,null,2); check(); });
  $('clear').addEventListener('click', () => { input.value = ''; current = null; render(); input.focus(); });
  input.addEventListener('input', () => { current = null; render(); });
  $('language').addEventListener('click', () => { lang = lang === 'en' ? 'zh' : 'en'; const url = new URL(location.href); if (lang === 'zh') url.searchParams.set('lang','zh'); else url.searchParams.delete('lang'); history.replaceState(null,'',url); render(); });
  render();
}());
