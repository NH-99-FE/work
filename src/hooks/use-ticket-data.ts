import { useState, useEffect } from 'react';
import type { TicketData, TicketMessage } from '@/types';

export function useTicketData() {
  const [data, setData] = useState<TicketData | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    fetch('./ticket_data.json')
      .then(res => {
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        return res.json();
      })
      .then(d => { setData(d); setLoading(false); prefetchMessages(); })
      .catch(e => { setError(e.message); setLoading(false); });
  }, []);

  return { data, loading, error };
}

// 按需加载 messages 索引，首屏 500ms 后预加载
let _messagesCache: Record<string, TicketMessage[]> | null = null;
let _messagesPromise: Promise<Record<string, TicketMessage[]>> | null = null;

function loadMessagesMap(): Promise<Record<string, TicketMessage[]>> {
  if (_messagesCache) return Promise.resolve(_messagesCache);
  if (_messagesPromise) return _messagesPromise;
  _messagesPromise = fetch('./ticket_messages.json')
    .then(res => {
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      return res.json() as Promise<Record<string, TicketMessage[]>>;
    })
    .then(map => {
      _messagesCache = map;
      return map;
    })
    .catch(e => {
      _messagesPromise = null;
      throw e;
    });
  return _messagesPromise;
}

// 首屏渲染后 500ms 预加载 messages
let _prefetched = false;
function prefetchMessages() {
  if (_prefetched) return;
  _prefetched = true;
  setTimeout(loadMessagesMap, 500);
}

export function useTicketMessages(ticketId: string | null) {
  const [state, setState] = useState<{ messages: TicketMessage[]; loading: boolean }>({
    messages: [],
    loading: false,
  });

  useEffect(() => {
    if (!ticketId) return;
    let cancelled = false;
    // 异步触发加载，避免 effect 内同步 setState
    Promise.resolve().then(() => {
      if (cancelled) return;
      setState({ messages: [], loading: true });
    });
    loadMessagesMap()
      .then(map => { if (!cancelled) setState({ messages: map[ticketId] ?? [], loading: false }); })
      .catch(() => { if (!cancelled) setState({ messages: [], loading: false }); });
    return () => { cancelled = true; };
  }, [ticketId]);

  return { messages: state.messages, loading: state.loading };
}
