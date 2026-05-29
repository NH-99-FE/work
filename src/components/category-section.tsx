import { Badge } from '@/components/ui/badge';
import { AccordionGroup } from '@/components/accordion-group';
import type { Category, Ticket } from '@/types';

export function CategorySection({ category, onSelectTicket }: {
  category: Category;
  onSelectTicket: (t: Ticket) => void;
}) {
  return (
    <div className="mb-8">
      <div className="flex items-center gap-2.5 mb-3">
        <h2 className="text-xl font-bold text-gray-900">{category.name}</h2>
        <Badge className="bg-indigo-50 text-indigo-600 px-2.5 py-0.5 rounded-full text-sm font-semibold border-0">
          {category.count}
        </Badge>
      </div>
      {category.groups.map(group => (
        <AccordionGroup
          key={group.id}
          group={group}
          onSelectTicket={onSelectTicket}
        />
      ))}
    </div>
  );
}
