import type { ReactNode } from 'react';

interface PanelProps {
  title?: ReactNode;
  action?: ReactNode;
  children: ReactNode;
  className?: string;
  flushHead?: boolean;
  bodyClassName?: string;
}

export default function Panel({ title, action, children, className, flushHead, bodyClassName }: PanelProps) {
  return (
    <section className={`panel ${className ?? ''}`}>
      {title || action ? (
        <header className={`panel__head ${flushHead ? 'panel__head--flush' : ''}`}>
          {typeof title === 'string' ? <h2>{title}</h2> : title}
          {action}
        </header>
      ) : null}
      <div className={bodyClassName}>{children}</div>
    </section>
  );
}
