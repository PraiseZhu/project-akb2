cindy.onHostMessage(async function (msg) {
  if (msg.type !== 'tool-call') return;
  if (msg.tool !== 'search' && msg.tool !== 'get') return;

  let cfg = {};
  try {
    cfg = await (await fetch('/kv')).json();
  } catch {
    cfg = {};
  }
  const host = String(cfg.host || '172.26.163.197');
  const port = Number(cfg.port || 8788);
  const method = msg.tool === 'get' ? 'get' : 'search';
  const params =
    method === 'get'
      ? { path: String(msg.args.path || ''), host, port }
      : {
          query: String(msg.args.query || ''),
          limit: msg.args.limit,
          host,
          port,
        };

  const response = await cindy.node.request({
    method,
    params,
    timeoutMs: 30000,
  });
  if (!response.ok) {
    await cindy.send({
      type: 'tool-result',
      callId: msg.callId,
      ok: false,
      errorCode: response.errorCode || 'NODE_REQUEST_FAILED',
      message: response.message || '检索失败',
    });
    return;
  }
  await cindy.send({
    type: 'tool-result',
    callId: msg.callId,
    ok: true,
    result: response.result,
  });
});
