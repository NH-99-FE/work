import { Dialog as DialogPrimitive } from 'radix-ui';
import { X } from 'lucide-react';

export function ImagePreview({ src, onClose }: {
  src: string | null;
  onClose: () => void;
}) {
  const open = !!src;

  return (
    <DialogPrimitive.Root open={open} onOpenChange={(v) => { if (!v) onClose(); }}>
      <DialogPrimitive.Portal>
        <DialogPrimitive.Overlay
          className="fixed inset-0 z-[100] bg-black/80 data-open:animate-in data-open:fade-in-0 data-closed:animate-out data-closed:fade-out-0"
        />
        <DialogPrimitive.Content
          aria-describedby={undefined}
          className="fixed inset-0 z-[100] flex items-center justify-center outline-none cursor-zoom-out"
          onClick={onClose}
        >
          <DialogPrimitive.Title className="sr-only">图片预览</DialogPrimitive.Title>
          {src && (
            <img
              src={src}
              alt="preview"
              className="max-w-[90vw] max-h-[90vh] object-contain rounded cursor-default"
              onClick={(e) => e.stopPropagation()}
            />
          )}
          <DialogPrimitive.Close
            className="absolute top-4 right-4 text-white/70 hover:text-white outline-none"
            aria-label="关闭"
          >
            <X size={24} />
          </DialogPrimitive.Close>
        </DialogPrimitive.Content>
      </DialogPrimitive.Portal>
    </DialogPrimitive.Root>
  );
}
