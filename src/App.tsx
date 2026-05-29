import { useState, useMemo } from 'react';
import { Search, X, Inbox } from 'lucide-react';
import { Input } from '@/components/ui/input';
import type { Ticket } from './types';

import { useTicketData } from '@/hooks/use-ticket-data';
import { LoadingState } from '@/components/loading-state';
import { ErrorState } from '@/components/error-state';
import { FilterChip } from '@/components/filter-chip';
import { CategorySection } from '@/components/category-section';
import { ConversationSheet } from '@/components/conversation-sheet';
import { ImagePreview } from '@/components/image-preview';

export default function App() {
  const { data, loading, error } = useTicketData();
  const [searchText, setSearchText] = useState('');
  const [selectedCategory, setSelectedCategory] = useState('全部');
  const [selectedTicket, setSelectedTicket] = useState<Ticket | null>(null);
  const [previewImage, setPreviewImage] = useState<string | null>(null);

  const categories = useMemo(() => {
    if (!data) return [];
    return data.categories.filter(c => c.count >= 5);
  }, [data]);

  const filteredData = useMemo(() => {
    let cats = categories;
    if (selectedCategory !== '全部') {
      cats = cats.filter(c => c.name === selectedCategory);
    }

    return cats.map(cat => {
      const filteredGroups = cat.groups.map(group => {
        let tickets = group.tickets;

        if (searchText.trim()) {
          const kw = searchText.trim().toLowerCase();
          tickets = tickets.filter(t =>
            t.title.toLowerCase().includes(kw) ||
            t.phenomenon.toLowerCase().includes(kw) ||
            t.id.includes(kw)
          );
        }

        return { ...group, tickets, count: tickets.length };
      }).filter(g => g.count > 0);

      return { ...cat, groups: filteredGroups, count: filteredGroups.reduce((s, g) => s + g.count, 0) };
    }).filter(c => c.count > 0);
  }, [categories, selectedCategory, searchText]);

  const totalFiltered = filteredData.reduce((s, c) => s + c.count, 0);

  if (loading) return <LoadingState />;
  if (error) return <ErrorState message={error} />;
  if (!data) return null;

  return (
    <div className="max-w-[900px] mx-auto px-4 py-6 min-h-screen">
      {/* 标题 */}
      <div className="mb-6">
        <h1 className="text-2xl font-bold text-gray-900">工单分析</h1>
        <p className="text-sm text-gray-400 mt-1.5">
          共 {data.summary.totalTickets} 条工单 · {data.summary.totalCategories} 个分类 · 当前显示 {totalFiltered} 条
        </p>
      </div>

      {/* 搜索框 */}
      <div className="relative mb-5">
        <Search className="absolute left-3.5 top-1/2 -translate-y-1/2 text-gray-400 z-10" size={16} />
        <Input
          className="w-full py-2.5 pl-10 pr-10 rounded-xl border-[1.5px] border-gray-200 focus-visible:border-indigo-500"
          placeholder="搜索工单标题、内容或编号..."
          value={searchText}
          onChange={e => setSearchText(e.target.value)}
        />
        {searchText && (
          <X className="absolute right-3.5 top-1/2 -translate-y-1/2 text-gray-300 cursor-pointer" size={16}
             onClick={() => setSearchText('')} />
        )}
      </div>

      {/* 一级分类筛选 */}
      <div className="mb-6 flex flex-wrap gap-2">
        <FilterChip active={selectedCategory === '全部'} onClick={() => setSelectedCategory('全部')}>全部</FilterChip>
        {categories.map(cat => (
          <FilterChip key={cat.id} active={selectedCategory === cat.name} onClick={() => setSelectedCategory(cat.name)}>
            {cat.name}
            <span className="ml-1 opacity-60 text-xs">{cat.count}</span>
          </FilterChip>
        ))}
      </div>

      {/* 分组列表 */}
      {filteredData.length === 0 ? (
        <div className="text-center py-16 text-gray-400">
          <Inbox className="mx-auto mb-4" size={48} />
          <p>没有匹配的工单</p>
        </div>
      ) : (
        filteredData.map(cat => (
          <CategorySection
            key={cat.id}
            category={cat}
            onSelectTicket={setSelectedTicket}
          />
        ))
      )}

      {/* 会话Sheet */}
      <ConversationSheet
        ticket={selectedTicket}
        open={!!selectedTicket}
        onOpenChange={(open) => { if (!open) setSelectedTicket(null); }}
        onPreviewImage={setPreviewImage}
      />

      {/* 图片预览 */}
      <ImagePreview src={previewImage} onClose={() => setPreviewImage(null)} />
    </div>
  );
}
