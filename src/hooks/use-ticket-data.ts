import { useState, useEffect } from 'react';
import type { TicketData } from '@/types';

// 构建时由 inject-data 脚本内联到 window.__TICKET_DATA__
declare global {
  interface Window {
    __TICKET_DATA__?: TicketData;
  }
}

export function useTicketData() {
  const [data, setData] = useState<TicketData | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    // 优先读取内联数据（支持 file:// 直接打开）
    if (window.__TICKET_DATA__) {
      setData(window.__TICKET_DATA__);
      setLoading(false);
      return;
    }
    // 回退到 fetch（开发模式 / 部署模式）
    fetch('./ticket_data.json')
      .then(res => {
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        return res.json();
      })
      .then(d => { setData(d); setLoading(false); })
      .catch(e => { setError(e.message); setLoading(false); });
  }, []);

  return { data, loading, error };
}
