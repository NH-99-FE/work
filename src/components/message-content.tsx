export function MessageContent({ content, type, onPreviewImage }: {
  content: string;
  type: string;
  onPreviewImage: (url: string) => void;
}) {
  // 图片消息
  if ((type === 'image' || content.includes('ruliu-img')) && content.includes('http')) {
    const urlMatch = content.match(/https?:\/\/[^\s]+/);
    if (urlMatch) {
      return (
        <img
          src={urlMatch[0]} alt="附件"
          className="max-w-[200px] max-h-[200px] object-cover rounded-lg cursor-pointer hover:opacity-90 transition-opacity"
          onClick={(e) => { e.stopPropagation(); onPreviewImage(urlMatch[0]); }}
          onError={(e) => { (e.target as HTMLImageElement).style.display = 'none'; }}
        />
      );
    }
  }

  // 代码块
  if (type === 'code' || content.includes('```')) {
    const parts = content.split('```');
    return <>{parts.map((part, i) => {
      if (i % 2 === 1) {
        const lines = part.split('\n');
        const lang = lines[0].trim();
        const code = lines.slice(lang ? 1 : 0).join('\n').trim();
        return (
          <pre key={i} className="bg-[#1e1e1e] text-[#d4d4d4] p-3 rounded-lg font-mono text-[13px] overflow-x-auto whitespace-pre-wrap my-2">
            {code}
          </pre>
        );
      }
      return <span key={i}>{renderInline(part, onPreviewImage)}</span>;
    })}</>;
  }

  return renderInline(content, onPreviewImage);
}

function renderInline(text: string, onPreviewImage: (url: string) => void) {
  // 图片链接
  const imgRegex = /(https?:\/\/[^\s]*ruliu-img[^\s]*)/g;
  const parts = text.split(imgRegex);
  if (parts.length > 1) {
    return <>{parts.map((part, i) => {
      if (part.includes('ruliu-img')) {
        return (
          <img key={i} src={part} alt="图片"
            className="max-w-[200px] max-h-[200px] object-cover rounded-lg cursor-pointer block my-1"
            onClick={(e) => { e.stopPropagation(); onPreviewImage(part); }}
            onError={(e) => { (e.target as HTMLImageElement).style.display = 'none'; }}
          />
        );
      }
      return <span key={i}>{part}</span>;
    })}</>;
  }

  // @提及
  let result = text.replace(/@(\S+)/g, '<mark class="bg-indigo-100 text-indigo-800 px-0.5 rounded">@$1</mark>');
  // [回复]
  result = result.replace(/\[回复\]/g, '<span class="text-indigo-500 text-xs">↩ 回复</span>');
  // 链接（排除已处理的图片链接）
  result = result.replace(/(?<!src=["'])((https?:\/\/(?!bj\.bcebos\.com\/v1\/ag-itsm)[^\s<]+))/g,
    '<a href="$1" target="_blank" class="text-indigo-600 underline">$1</a>');

  return <span dangerouslySetInnerHTML={{ __html: result }} />;
}
