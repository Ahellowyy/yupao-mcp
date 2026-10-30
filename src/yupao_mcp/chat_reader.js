/* First-party adapter. Only login and read operations are exposed. */
(() => {
  const sdk = globalThis.TencentCloudChat;
  const state = { chat: null, stopped: false };
  const clip = (value, limit = 500) => typeof value === 'string' ? value.slice(0, limit) : '';
  const message = (item) => {
    let customType = null;
    if (item.type === sdk.TYPES.MSG_CUSTOM && typeof item.payload?.data === 'string') {
      try { customType = JSON.parse(item.payload.data).type ?? null; } catch { /* no raw payload */ }
    }
    return {
      message_id: clip(item.ID, 200), conversation_id: clip(item.conversationID, 200),
      from: clip(item.from, 128), to: clip(item.to, 128), flow: clip(item.flow, 8),
      time: item.time ?? null, sequence: item.sequence ?? null, type: clip(item.type, 64),
      text: item.type === sdk.TYPES.MSG_TEXT ? clip(item.payload?.text, 5000) : '',
      custom_type: typeof customType === 'number' || typeof customType === 'string' ? customType : null,
      status: clip(item.status, 32), is_revoked: item.isRevoked === true,
      is_peer_read: typeof item.isPeerRead === 'boolean' ? item.isPeerRead : null,
    };
  };
  const conversation = (item) => {
    const last = item.lastMessage || {};
    return {
      conversation_id: clip(item.conversationID, 200), type: clip(item.type, 32),
      peer_im_id: clip(item.userProfile?.userID, 128),
      name: clip(item.remark || item.userProfile?.nick, 120),
      unread_count: item.unreadCount ?? 0,
      last_message: {
        from: clip(last.fromAccount, 128), time: last.lastTime ?? null,
        sequence: last.lastSequence ?? null, type: clip(last.type, 64),
        preview: last.type === sdk.TYPES.MSG_TEXT ? clip(last.messageForShow, 500) : '',
        is_peer_read: typeof last.isPeerRead === 'boolean' ? last.isPeerRead : null,
        is_revoked: last.isRevoked === true,
      },
    };
  };
  globalThis.__yupaoSdkLogin = async ({ appID, userID, userSig }) => {
    if (state.chat) return { ok: false, code: 'SDK_ALREADY_INITIALIZED' };
    state.chat = sdk.create({ SDKAppID: appID });
    state.chat.setLogLevel(4);
    state.chat.on(sdk.EVENT.KICKED_OUT, () => { state.stopped = true; });
    return new Promise((resolve) => {
      let settled = false;
      const finish = (result) => { if (!settled) { settled = true; clearTimeout(timer); resolve(result); } };
      const timer = setTimeout(() => finish({ ok: false, code: 'SDK_LOGIN_TIMEOUT' }), 30000);
      state.chat.on(sdk.EVENT.SDK_READY, () => finish({ ok: true, ready: true, version: sdk.VERSION }));
      state.chat.login({ userID, userSig }).then(() => {
        if (state.chat.isReady()) finish({ ok: true, ready: true, version: sdk.VERSION });
      }).catch((error) => finish({ ok: false, code: String(error?.code || 'SDK_LOGIN_FAILED') }));
    });
  };
  globalThis.__yupaoSdkRead = async ({ operation, args }) => {
    if (state.stopped || !state.chat?.isReady()) return { ok: false, code: 'SDK_NOT_READY' };
    try {
      if (operation === 'conversations') {
        let data;
        // The SDK syncs its own list after login. Wait for its completion flag;
        // an early empty cache is not proof of an empty account.
        for (let attempt = 0; attempt < 60; attempt++) {
          data = (await state.chat.getConversationList()).data;
          if (data?.isSyncCompleted === true) break;
          await new Promise((resolve) => setTimeout(resolve, 250));
        }
        if (!Array.isArray(data?.conversationList) || data.isSyncCompleted !== true)
          return { ok: false, code: 'SDK_SYNC_INCOMPLETE' };
        return { ok: true, data: { is_sync_completed: true,
          conversations: data.conversationList.map(conversation) } };
      }
      if (operation === 'messages') {
        if (!/^C2C[A-Za-z0-9_-]{1,128}$/.test(args.conversation_id))
          return { ok: false, code: 'INVALID_CONVERSATION' };
        const list = (await state.chat.getConversationList()).data;
        if (list?.isSyncCompleted !== true || !list.conversationList.some(
          (item) => item.conversationID === args.conversation_id))
          return { ok: false, code: 'CONVERSATION_NOT_IN_SYNCED_LIST' };
        const options = { conversationID: args.conversation_id };
        if (args.next_req_message_id) options.nextReqMessageID = args.next_req_message_id;
        const data = (await state.chat.getMessageList(options)).data;
        if (!Array.isArray(data?.messageList) || typeof data.isCompleted !== 'boolean')
          return { ok: false, code: 'SDK_SCHEMA_CHANGED' };
        return { ok: true, data: { conversation_id: args.conversation_id,
          messages: data.messageList.map(message), is_completed: data.isCompleted,
          next_req_message_id: clip(data.nextReqMessageID, 500) } };
      }
      return { ok: false, code: 'SDK_OPERATION_NOT_ALLOWED' };
    } catch (error) {
      state.stopped = true;
      return { ok: false, code: String(error?.code || 'SDK_READ_FAILED') };
    }
  };
  globalThis.__yupaoSdkClose = async () => {
    state.stopped = true;
    if (state.chat) await state.chat.destroy();
  };
})();
