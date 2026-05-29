import { cn } from '@/lib/utils';

export function FilterChip({ active, onClick, children, small }: {
  active: boolean; onClick: () => void; children: React.ReactNode; small?: boolean;
}) {
  return (
    <button
      type="button"
      className={cn(
        'inline-flex items-center cursor-pointer rounded-full border-[1.5px] transition-all select-none',
        small ? 'px-3 py-1 text-xs' : 'px-4 py-1.5 text-sm',
        active
          ? 'bg-indigo-600 text-white border-indigo-600'
          : 'bg-white text-gray-700 border-gray-200 hover:border-indigo-400 hover:text-indigo-500',
      )}
      onClick={onClick}
    >
      {children}
    </button>
  );
}
