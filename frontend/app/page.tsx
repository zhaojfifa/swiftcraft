import Link from 'next/link';
import { Sparkles, ArrowRight } from 'lucide-react';

export default function Home() {
  return (
    <div className='min-h-screen bg-white text-slate-900 font-sans selection:bg-blue-100'>
      <header className='h-16 flex items-center justify-between px-6 bg-white border-b border-slate-200 sticky top-0 z-50'>
        <div className='flex items-center gap-2'>
          <div className='bg-blue-600 p-1.5 rounded-lg'>
            <Sparkles className='w-4 h-4 text-white' />
          </div>
          <span className='font-bold text-lg tracking-tight text-slate-900'>SwiftCraft</span>
        </div>
        <div className='bg-slate-50 px-3 py-1 rounded-full text-xs font-medium text-slate-600 border border-slate-200'>
          AI Video Generation Demo
        </div>
      </header>

      <main className='max-w-5xl mx-auto mt-24 px-6'>
        <div className='text-center mb-16'>
          <h1 className='text-4xl font-extrabold mb-4 text-slate-900 tracking-tight'>
            Select a Service
          </h1>
          <p className='text-slate-500 text-lg'>
            Choose a pipeline to start your generation task.
          </p>
        </div>

        <div className='grid grid-cols-1 md:grid-cols-2 gap-6'>
          <Link href="/workspace?service=swap"
            className="group relative block rounded-2xl border border-emerald-200/20 bg-gradient-to-br from-emerald-950 via-slate-950 to-slate-950 p-8 overflow-hidden
                      shadow-[0_18px_60px_rgba(16,185,129,0.18)] hover:shadow-[0_22px_80px_rgba(16,185,129,0.24)]
                      hover:-translate-y-1 transition-all duration-300"
          >
            {/* subtle glow */}
            <div className="pointer-events-none absolute -top-24 -left-24 h-56 w-56 rounded-full bg-emerald-400/20 blur-3xl opacity-60 group-hover:opacity-80 transition-opacity" />
            <div className="pointer-events-none absolute inset-0 bg-gradient-to-t from-black/30 via-transparent to-white/5" />

            <div className="relative z-10 flex justify-between items-start mb-12">
              <div className="p-3 rounded-xl bg-white/5 border border-white/10">
                <div className="w-6 h-6 rounded-full border-2 border-emerald-300/30" />
              </div>
              <span className="text-[10px] font-bold text-emerald-200 bg-white/5 px-2 py-1 rounded border border-white/10 uppercase tracking-wider">
                Service
              </span>
            </div>

            <h2 className="relative z-10 text-2xl font-bold text-white mb-2 group-hover:text-emerald-200 transition-colors">
              Swap
            </h2>
            <p className="relative z-10 text-slate-200/80 leading-relaxed mb-6">
              Replace subject with target identity while preserving motion.
            </p>

            <div className="relative z-10 flex items-center text-emerald-200 font-medium text-sm group-hover:underline underline-offset-4">
              Enter Workspace <span className="ml-2">→</span>
            </div>
          </Link>

          <Link href="/workspace?service=avatar"
            className="group relative block rounded-2xl border border-rose-200/20 bg-gradient-to-br from-rose-950 via-slate-950 to-slate-950 p-8 overflow-hidden
                      shadow-[0_18px_60px_rgba(244,63,94,0.18)] hover:shadow-[0_22px_80px_rgba(244,63,94,0.24)]
                      hover:-translate-y-1 transition-all duration-300"
          >
            {/* subtle glow */}
            <div className="pointer-events-none absolute -top-24 -left-24 h-56 w-56 rounded-full bg-rose-400/20 blur-3xl opacity-60 group-hover:opacity-80 transition-opacity" />
            <div className="pointer-events-none absolute inset-0 bg-gradient-to-t from-black/30 via-transparent to-white/5" />

            <div className="relative z-10 flex justify-between items-start mb-12">
              <div className="p-3 rounded-xl bg-white/5 border border-white/10">
                <div className="w-6 h-6 rounded-full border-2 border-rose-300/30" />
              </div>
              <span className="text-[10px] font-bold text-rose-200 bg-white/5 px-2 py-1 rounded border border-white/10 uppercase tracking-wider">
                Service
              </span>
            </div>

            <h2 className="relative z-10 text-2xl font-bold text-white mb-2 group-hover:text-rose-200 transition-colors">
              Avatar
            </h2>
            <p className="relative z-10 text-slate-200/80 leading-relaxed mb-6">
              Generate a stylized avatar track with adaptive lighting.
            </p>

            <div className="relative z-10 flex items-center text-rose-200 font-medium text-sm group-hover:underline underline-offset-4">
              Enter Workspace <span className="ml-2">→</span>
            </div>
          </Link>
        </div>
      </main>
    </div>
  );
}
