function sourceUrl(value: string) {
  try {
    const parsed = new URL(value);
    return ['http:', 'https:'].includes(parsed.protocol) ? parsed : null;
  } catch {
    return null;
  }
}

export function isBundledDemoSource(value: string) {
  return sourceUrl(value)?.hostname === 'demo.sanyojan.test';
}

interface Props {
  url: string;
  compact?: boolean;
  extractedAt?: string;
}

export function SourceReference({ url, compact = false, extractedAt }: Props) {
  const parsed = sourceUrl(url);
  const timestamp = extractedAt ? `Extracted ${extractedAt}` : undefined;

  if (isBundledDemoSource(url)) {
    return (
      <span className="source-reference bundled-source" title={timestamp ?? 'Served locally by Sanyojan, not a public website.'}>
        <span className="fixture-tag">Bundled demo fixture</span>
        {!compact && <span className="source-url">{url}</span>}
      </span>
    );
  }

  if (!parsed) {
    return <span className="source-reference muted" title={timestamp}>{compact ? 'Source unavailable' : url}</span>;
  }

  return (
    <a className="source-reference external-source" href={url} target="_blank" rel="noreferrer" title={timestamp}>
      {compact ? 'View page' : url} <span aria-hidden="true">↗</span>
    </a>
  );
}
