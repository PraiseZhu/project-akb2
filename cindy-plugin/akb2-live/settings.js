const $ = (s) => document.querySelector(s);

async function load() {
  const cfg = await (await fetch('/kv')).json();
  $('#host').value = cfg.host || '172.26.163.197';
  $('#port').value = cfg.port || 8787;
  const secrets = await (await fetch('/secrets')).json();
  const row = Array.isArray(secrets)
    ? secrets.find((item) => item.key === 'retrieval_token')
    : secrets;
  const saved = Boolean(row && (row.saved === true || row.key === 'retrieval_token'));
  $('#token-state').textContent = saved ? 'token 已保存（不回显明文）' : '尚未保存 token';
}

$('#save').onclick = async () => {
  const host = $('#host').value.trim() || '172.26.163.197';
  const port = Number($('#port').value || 8787);
  await fetch('/kv', {
    method: 'PUT',
    body: JSON.stringify({ host, port }),
  });
  const token = $('#token').value;
  if (token) {
    await fetch('/secrets/retrieval_token', {
      method: 'PUT',
      body: JSON.stringify({ value: token }),
    });
    $('#token').value = '';
  }
  $('#status').textContent = '已保存';
  await load();
};

load();
