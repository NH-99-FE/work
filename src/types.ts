export interface TicketMessage {
  sender: string;
  time: string;
  content: string;
  type: 'text' | 'image' | 'code' | 'reply' | 'link';
  role: 'user' | 'robot';
}

export interface Ticket {
  id: string;
  title: string;
  phenomenon: string;
  status: string;
  customerService: string;
  source: string;
  satisfaction: number;
  feedbackTime: string;
  responseTime: string;
  processDuration: string;
  ticketType: string;
  messages?: TicketMessage[];  // 按需加载，首屏不含
}

export interface Group {
  id: string;
  name: string;
  count: number;
  tickets: Ticket[];
}

export interface Category {
  id: string;
  name: string;
  count: number;
  ticketTypes: string[];
  groups: Group[];
}

export interface TicketData {
  summary: {
    totalTickets: number;
    totalCategories: number;
    totalGroups: number;
    ticketTypes: string[];
  };
  categories: Category[];
}
