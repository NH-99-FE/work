import { User, Clock } from 'lucide-react';
import { Badge } from '@/components/ui/badge';
import type { Ticket } from '@/types';

function formatTime(t: string) {
  if (!t) return '-';
  return t.replace('小时', 'h').replace('分钟', 'm').replace('秒', 's');
}

const typeColor: Record<string, string> = {
  '质量类': 'bg-amber-100 text-amber-700',
  '问题咨询类': 'bg-blue-100 text-blue-700',
  '通用类': 'bg-green-100 text-green-700',
  '需求类': 'bg-pink-100 text-pink-700',
};

export function TicketRow({ ticket, onClick }: { ticket: Ticket; onClick: () => void }) {
  const isProcessing = ticket.status === '处理中';
  const date = ticket.feedbackTime ? ticket.feedbackTime.slice(5, 10) : '';

  return (
    <div
      className="px-4 py-3 border-t border-gray-50 cursor-pointer hover:bg-indigo-50/30 transition-colors"
      onClick={onClick}
    >
      <div className="flex items-center gap-2">
        <span className={`w-2 h-2 rounded-full shrink-0 ${isProcessing ? 'bg-amber-400' : 'bg-green-500'}`} />
        <span className="text-sm text-gray-700 truncate flex-1">{ticket.title || '(无标题)'}</span>
        {ticket.ticketType && (
          <span className={`text-[11px] px-2 py-0.5 rounded shrink-0 ${typeColor[ticket.ticketType] || 'bg-gray-100 text-gray-600'}`}>
            {ticket.ticketType}
          </span>
        )}
      </div>
      <div className="flex items-center gap-3 mt-1.5 ml-4 text-xs text-gray-400">
        <span className="flex items-center gap-1"><User size={12} />{ticket.customerService}</span>
        <span className="flex items-center gap-1"><Clock size={12} />{formatTime(ticket.processDuration)}</span>
        <span>{date}</span>
        <Badge
          variant="outline"
          className={`text-[11px] px-1.5 py-0.5 rounded-md border-0 ${
            isProcessing ? 'bg-amber-50 text-amber-700' : 'bg-green-50 text-green-700'
          }`}
        >
          {ticket.status}
        </Badge>
      </div>
    </div>
  );
}
