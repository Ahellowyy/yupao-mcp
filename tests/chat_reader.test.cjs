const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

const adapter = fs.readFileSync(path.join(__dirname, '../src/yupao_mcp/chat_reader.js'), 'utf8');

async function setup(options = {}) {
  const events = {};
  const calls = [];
  let ready = false;
  const rows = options.rows || [{ conversationID: 'C2Cpeer', type: 'C2C', unreadCount: 3,
    userProfile: { userID: 'peer', nick: '示例 HR' },
    lastMessage: { type: 'TIMTextElem', messageForShow: '您好', fromAccount: 'peer' } }];
  const chat = {
    setLogLevel() {}, on(event, callback) { events[event] = callback; },
    async login() { ready = true; events.ready(); }, isReady() { return ready; },
    async getConversationList() {
      calls.push('getConversationList');
      if (options.error) throw { code: 999, message: 'private-user-sig' };
      return { data: { conversationList: rows,
        isSyncCompleted: !options.firstIncomplete || calls.length > 1 } };
    },
    async getMessageList() {
      calls.push('getMessageList');
      return { data: { messageList: options.messages || [], isCompleted: true, nextReqMessageID: '' } };
    },
    async destroy() { ready = false; },
    setMessageRead() { throw Error('Must not mark read'); },
    sendMessageReadReceipt() { throw Error('Must not send read receipts'); },
    sendMessage() { throw Error('Must not send messages'); },
  };
  const context = vm.createContext({ setTimeout, clearTimeout,
    TencentCloudChat: { VERSION: 'test', TYPES: { MSG_TEXT: 'TIMTextElem', MSG_CUSTOM: 'TIMCustomElem' },
      EVENT: { SDK_READY: 'ready', KICKED_OUT: 'kicked' }, create: () => chat } });
  vm.runInContext(adapter, context);
  const login = await context.__yupaoSdkLogin({ appID: 1, userID: 'self', userSig: 'test-only' });
  assert.equal(login.ready, true);
  return { context, calls, rows };
}

test('unknown operations cannot reach a write API', async () => {
  const { context, calls } = await setup();
  for (const operation of ['sendMessage', 'setMessageRead', 'sendMessageReadReceipt']) {
    const result = await context.__yupaoSdkRead({ operation, args: {} });
    assert.equal(result.code, 'SDK_OPERATION_NOT_ALLOWED');
  }
  assert.equal(calls.length, 0);
});

test('conversation reads wait for SDK synchronization and preserve unread counts', async () => {
  const { context, rows, calls } = await setup({ firstIncomplete: true });
  const result = await context.__yupaoSdkRead({ operation: 'conversations', args: {} });
  assert.equal(result.data.is_sync_completed, true);
  assert.equal(result.data.conversations[0].name, '示例 HR');
  assert.equal(calls.length, 2);
  assert.equal(rows[0].unreadCount, 3);
});

test('message reads are limited to existing synchronized C2C conversations', async () => {
  const { context, calls } = await setup();
  const result = await context.__yupaoSdkRead({ operation: 'messages',
    args: { conversation_id: 'C2Cunrelated' } });
  assert.equal(result.code, 'CONVERSATION_NOT_IN_SYNCED_LIST');
  assert.equal(calls.includes('getMessageList'), false);
});

test('message normalization omits raw custom payloads and attachment URLs', async () => {
  const { context, rows } = await setup({ messages: [
    { ID: 'message1', conversationID: 'C2Cpeer', type: 'TIMTextElem', payload: { text: '您好' } },
    { ID: 'message2', conversationID: 'C2Cpeer', type: 'TIMCustomElem', payload: {
      data: JSON.stringify({ type: 17, token: 'private-user-sig', url: 'private-download' }) } },
  ] });
  const result = await context.__yupaoSdkRead({ operation: 'messages', args: { conversation_id: 'C2Cpeer' } });
  assert.equal(result.data.messages[0].text, '您好');
  assert.equal(result.data.messages[1].custom_type, 17);
  assert.equal(JSON.stringify(result).includes('private-'), false);
  assert.equal(rows[0].unreadCount, 3);
});

test('SDK error text is redacted and further reads stop', async () => {
  const { context, calls } = await setup({ error: true });
  const first = await context.__yupaoSdkRead({ operation: 'conversations', args: {} });
  const second = await context.__yupaoSdkRead({ operation: 'conversations', args: {} });
  assert.equal(first.code, '999');
  assert.equal(second.code, 'SDK_NOT_READY');
  assert.equal(JSON.stringify(first).includes('private-'), false);
  assert.equal(calls.length, 1);
});
