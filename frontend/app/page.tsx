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
          <Link href='/workspace?service=swap' className='group relative block rounded-2xl border border-slate-200 bg-white p-8 hover:shadow-xl hover:-translate-y-1 transition-all duration-300 cursor-pointer overflow-hidden hover:border-emerald-200'>
            <div className='absolute top-0 left-0 w-full h-1 bg-gradient-to-r from-emerald-400 to-teal-500 opacity-0 group-hover:opacity-100 transition-opacity' />

            <div className='flex justify-between items-start mb-12'>
              <div className='p-3 bg-emerald-50 rounded-xl group-hover:bg-emerald-100 transition-colors'>
                <div className='w-6 h-6 rounded-full border-2 border-emerald-500/30' />
              </div>
              <span className='text-[10px] font-bold text-emerald-700 bg-emerald-50 px-2 py-1 rounded border border-emerald-100 uppercase tracking-wider'>
                Service
              </span>
            </div>

            <h2 className='text-2xl font-bold text-slate-900 mb-2'>Swap</h2>
            <p className='text-slate-500 leading-relaxed mb-6'>
              Replace subject with target identity while preserving motion.
            </p>

            <div className='flex items-center text-emerald-600 font-medium text-sm group-hover:underline'>
              Enter Workspace <ArrowRight className='w-4 h-4 ml-1' />
            </div>
          </Link>

          <Link href='/workspace?service=avatar' className='group relative block rounded-2xl border border-slate-200 bg-white p-8 hover:shadow-xl hover:-translate-y-1 transition-all duration-300 cursor-pointer overflow-hidden hover:border-rose-200'>
            <div className='absolute top-0 left-0 w-full h-1 bg-gradient-to-r from-rose-400 to-purple-500 opacity-0 group-hover:opacity-100 transition-opacity' />

            <div className='flex justify-between items-start mb-12'>
              <div className='p-3 bg-rose-50 rounded-xl group-hover:bg-rose-100 transition-colors'>
                <div className='w-6 h-6 rounded-full border-2 border-rose-500/30' />
              </div>
              <span className='text-[10px] font-bold text-rose-700 bg-rose-50 px-2 py-1 rounded border border-rose-100 uppercase tracking-wider'>
                Service
              </span>
            </div>

            <h2 className='text-2xl font-bold text-slate-900 mb-2'>Avatar</h2>
            <p className='text-slate-500 leading-relaxed mb-6'>
              Generate a stylized avatar track with adaptive lighting.
            </p>

            <div className='flex items-center text-rose-600 font-medium text-sm group-hover:underline'>
              Enter Workspace <ArrowRight className='w-4 h-4 ml-1' />
            </div>
          </Link>
        </div>
      </main>
    </div>
  );
}
