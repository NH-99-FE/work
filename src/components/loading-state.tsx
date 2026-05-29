import { Loader2 } from 'lucide-react';

export function LoadingState() {
  return (
    <div className="flex items-center justify-center min-h-screen">
      <div className="text-center">
        <Loader2 className="mx-auto mb-3 text-indigo-500 animate-spin" size={32} />
        <p className="text-gray-400">加载数据中...</p>
      </div>
    </div>
  );
}
