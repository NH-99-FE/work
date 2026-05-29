import { CircleAlert } from 'lucide-react';

export function ErrorState({ message }: { message: string }) {
  return (
    <div className="flex items-center justify-center min-h-screen">
      <div className="text-center text-red-500">
        <CircleAlert className="mx-auto mb-3" size={32} />
        <p>加载失败: {message}</p>
      </div>
    </div>
  );
}
