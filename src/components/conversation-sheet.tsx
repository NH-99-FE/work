import { User, Clock, AlertTriangle } from 'lucide-react';
import {
  Sheet,
  SheetContent,
  SheetHeader,
  SheetTitle,
  SheetDescription,
} from '@/components/ui/sheet';
import { MessageContent } from '@/components/message-content';
import type { Ticket } from '@/types';

function formatTime(t: string) {
  if (!t) return '-';
  return t.replace('小时', 'h').replace('分钟', 'm').replace('秒', 's');
}

export function ConversationSheet({ ticket, open, onOpenChange, onPreviewImage }: {
  ticket: Ticket | null;
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onPreviewImage: (url: string) => void;
}) {
  if (!ticket) return null;

  // 第一个非 robot 发言的人是用户，其余 user 是客服
  const firstSender = ticket.messages.find(m => m.role !== 'robot')?.sender;  return (
    <Sheet open={open} onOpenChange={onOpenChange}>
      <SheetContent
        side="right"
        className="w-[560px] max-w-[90vw] sm:max-w-[560px] p-0 gap-0 overflow-y-auto"
        showCloseButton
      >
        <SheetHeader className="px-5 pt-5 pb-4 border-b border-gray-100 space-y-0">
          <SheetTitle className="text-base font-semibold text-gray-900 leading-snug text-left pr-8">
            {ticket.title}
          </SheetTitle>
          <SheetDescription className="sr-only">工单会话详情</SheetDescription>
          <div className="flex flex-wrap gap-x-3 gap-y-1 mt-2 text-xs text-gray-400">
            <span className="flex items-center gap-1">
              <span className={`w-1.5 h-1.5 rounded-full ${ticket.status === '处理中' ? 'bg-amber-400' : 'bg-green-500'}`} />
              {ticket.status}
            </span>
            <span className="flex items-center gap-1"><User size={11} />{ticket.customerService}</span>
            <span className="flex items-center gap-1"><Clock size={11} />{formatTime(ticket.processDuration)}</span>
            <span>{ticket.feedbackTime?.slice(0, 16)}</span>
          </div>
        </SheetHeader>

        {ticket.phenomenon && (
          <div className="px-5 py-3 bg-amber-50 border-b border-amber-100 text-[13px] text-amber-800">
            <AlertTriangle className="inline mr-1.5" size={14} />{ticket.phenomenon}
          </div>
        )}

        <div className="px-5 py-4">
          {ticket.messages.length === 0 ? (
            <div className="text-center py-10 text-gray-400">暂无会话记录</div>
          ) : (
            ticket.messages.map((msg, idx) => {
              const isUser = msg.role === 'user' && (msg.sender === firstSender);
              return (
                <div key={idx} className={`flex flex-col ${isUser ? 'items-start' : 'items-end'} mb-3`}>
                  <div className="text-[11px] text-gray-300 mb-1 px-1">
                    {msg.sender} · {msg.time?.slice(11, 16)}
                  </div>
                  <div className={`max-w-[85%] px-3.5 py-2.5 rounded-xl text-sm break-words leading-relaxed ${
                    isUser
                      ? 'bg-gray-50 text-gray-800 rounded-bl-sm'
                      : msg.role === 'robot'
                        ? 'bg-blue-50 text-gray-800 rounded-br-sm'
                        : 'bg-indigo-50 text-gray-800 rounded-br-sm'
                  }`}>
                    <MessageContent content={msg.content} type={msg.type} onPreviewImage={onPreviewImage} />
                  </div>
                </div>
              );
            })
          )}
        </div>
      </SheetContent>
    </Sheet>
  );
}
