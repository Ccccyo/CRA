import { CraBrand } from '@/features/cra/brand';

import { LocaleSelector } from '@/shared/blocks/common';

export default function AuthLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <div className="bg-background text-foreground selection:bg-primary selection:text-primary-foreground min-h-dvh">
      <header className="border-border/80 bg-background/95 absolute inset-x-0 top-0 z-10 h-20 border-b">
        <div className="mx-auto flex h-full max-w-[1240px] items-center justify-between px-5 md:px-8">
          <CraBrand />
          <LocaleSelector type="button" />
        </div>
      </header>
      <main className="flex min-h-dvh items-center px-4 pt-28 pb-10 md:px-8 lg:pt-24 lg:pb-8">
        {children}
      </main>
    </div>
  );
}
