/* First-party adapter. Reads and one internal confirmation-gated text sender. */
(() => {
  const sdk = globalThis.TencentCloudChat;
  const state = { chat: null, stopped: false };
  const clip = (value, limit = 500) => typeof value === 'string' ? value.slice(0, limit) : '';
  const message = (item) => {
    let customType = null;
    let resumeCard = null;
    if (item.type === sdk.TYPES.MSG_CUSTOM && typeof item.payload?.data === 'string') {
      try {
        const custom = JSON.parse(item.payload.data);
        customType = custom?.type ?? null;
        if (String(customType) === '710.1' && custom.content && typeof custom.content === 'object') {
          const content = custom.content;
          const id = (value, limit = 200) => typeof value === 'string'
            && new RegExp('^[A-Za-z0-9_:\\-]{1,' + limit + '}$').test(value) ? value : '';
          resumeCard = {
            sub_type: ['1', '2', '3'].includes(String(content.subType)) ? String(content.subType) : '',
            resume_file_id: id(content.fileInfo?.fileId),
            file_name: clip(content.fileInfo?.fileName, 200),
            exchange_message_id: id(content.exchangeMsgId, 500),
          };
        }
      } catch { /* no raw payload */ }
    }
    return {
      message_id: clip(item.ID, 200), conversation_id: clip(item.conversationID, 200),
      from: clip(item.from, 128), to: clip(item.to, 128), flow: clip(item.flow, 8),
      time: item.time ?? null, sequence: item.sequence ?? null, type: clip(item.type, 64),
      text: item.type === sdk.TYPES.MSG_TEXT ? clip(item.payload?.text, 5000) : '',
      custom_type: typeof customType === 'number' || typeof customType === 'string' ? customType : null,
      resume_card: resumeCard,
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
  // This entry point is not exposed as an MCP tool. The Python action service
  // calls it only after a reviewed, approved, account-bound atomic reservation.
  globalThis.__yupaoSdkSendConfirmed = async ({ conversation_id, text, allow_new_peer = false }) => {
    if (state.stopped || !state.chat?.isReady()) return { ok: false, code: 'SDK_NOT_READY', send_invoked: false };
    if (typeof text !== 'string' || !text.trim() || text.length > 5000
        || !/^C2C[A-Za-z0-9_-]{1,128}$/.test(conversation_id)
        || ['C2Cyupao625e3dg36dgsh', 'C2Cyupao625e6fc83ndsa'].includes(conversation_id)
        || conversation_id.startsWith('C2Cagent_')) return { ok: false, code: 'INVALID_TARGET', send_invoked: false };
    let sendInvoked = false;
    let plannedMessageID = '';
    try {
      // SDK_READY does not mean that its separate conversation synchronization
      // has finished. Use the same bounded wait as the read path before sending.
      let list;
      for (let attempt = 0; attempt < 60; attempt++) {
        list = (await state.chat.getConversationList()).data;
        if (list?.isSyncCompleted === true) break;
        await new Promise((resolve) => setTimeout(resolve, 250));
      }
      if (list?.isSyncCompleted !== true)
        return { ok: false, code: 'SDK_SYNC_INCOMPLETE', send_invoked: false };
      let target = list?.conversationList?.find((item) => item.conversationID === conversation_id);
      // Only the internal action executor can set allow_new_peer, after creating
      // a business conversation and binding its metadata to the approved job.
      if (!target && allow_new_peer === true)
        target = { type: sdk.TYPES.CONV_C2C, userProfile: { userID: conversation_id.slice(3) } };
      if (list?.isSyncCompleted !== true || !target || target.type !== sdk.TYPES.CONV_C2C
          || !target.userProfile?.userID || 'C2C' + target.userProfile.userID !== conversation_id)
        return { ok: false, code: 'CONVERSATION_NOT_IN_SYNCED_LIST', send_invoked: false };
      const draft = state.chat.createTextMessage({ to: target.userProfile.userID,
        conversationType: sdk.TYPES.CONV_C2C, needReadReceipt: true, payload: { text } });
      if (!draft?.ID) return { ok: false, code: 'SDK_DRAFT_INVALID', send_invoked: false };
      plannedMessageID = draft.ID;
      sendInvoked = true;
      const result = await state.chat.sendMessage(draft); // exactly one call, never resend
      const sent = result?.data?.message;
      if (!sent?.ID || sent.to !== target.userProfile.userID || sent.payload?.text !== text)
        return { ok: false, code: 'SDK_SEND_RESULT_UNKNOWN', send_invoked: true, planned_message_id: plannedMessageID };
      return { ok: true, data: { message_id: sent.ID, to: sent.to,
        acknowledged: true, delivery_verified: false, is_peer_read: sent.isPeerRead === true } };
    } catch (error) {
      state.stopped = true;
      return { ok: false, code: String(error?.code || 'SDK_SEND_RESULT_UNKNOWN'),
        send_invoked: sendInvoked, planned_message_id: plannedMessageID };
    }
  };
  globalThis.__yupaoSdkClose = async () => {
    state.stopped = true;
    if (state.chat) await state.chat.destroy();
  };
})();
