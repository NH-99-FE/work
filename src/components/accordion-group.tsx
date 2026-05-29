import {
  Accordion,
  AccordionItem,
  AccordionTrigger,
  AccordionContent,
} from '@/components/ui/accordion';
import type { Group, Ticket } from '@/types';
import { TicketRow } from '@/components/ticket-row';

export function AccordionGroup({ group, onSelectTicket }: {
  group: Group;
  onSelectTicket: (t: Ticket) => void;
}) {
  return (
    <div className="mb-2 bg-white rounded-xl overflow-hidden shadow-[0_1px_3px_rgba(0,0,0,0.06)]">
      <Accordion type="multiple" defaultValue={[group.id]}>
        <AccordionItem value={group.id} className="border-b-0">
          <AccordionTrigger className="px-4 py-3 hover:no-underline hover:bg-gray-50/50 rounded-none">
            <div className="flex items-center gap-2.5 flex-1 min-w-0">
              <span className="font-semibold text-[15px] text-gray-800">{group.name}</span>
              <span className="text-sm text-gray-400">{group.count}条</span>
            </div>
          </AccordionTrigger>
          <AccordionContent className="pb-0">
            {group.tickets.map(ticket => (
              <TicketRow key={ticket.id} ticket={ticket} onClick={() => onSelectTicket(ticket)} />
            ))}
          </AccordionContent>
        </AccordionItem>
      </Accordion>
    </div>
  );
}
