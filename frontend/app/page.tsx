import Link from 'next/link';
import { Sparkles } from 'lucide-react';

import { SERVICE_REGISTRY } from '../lib/services/registry';

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
          {SERVICE_REGISTRY.map((service) => (
            <Link key={service.id} href={service.route} className={service.ui?.cardClass || ""}>
              {/* subtle glow */}
              <div className={service.ui?.glowClass || ""} />
              <div className="pointer-events-none absolute inset-0 bg-gradient-to-t from-black/30 via-transparent to-white/5" />

              <div className="relative z-10 flex justify-between items-start mb-12">
                <div className="p-3 rounded-xl bg-white/5 border border-white/10">
                  <div className="w-6 h-6 rounded-full border-2 border-white/30" />
                </div>
                <span className={service.ui?.badgeClass || ""}>{service.badge}</span>
              </div>

              <h2 className={`relative z-10 ${service.ui?.titleClass || ""}`}>
                {service.title}
              </h2>
              <p className="relative z-10 text-slate-200/80 leading-relaxed mb-6">
                {service.description}
              </p>

              <div className={service.ui?.ctaClass || ""}>
                {service.ui?.ctaLabel || "Enter Workspace"} <span className="ml-2">{"→"}</span>
              </div>
            </Link>
          ))}
        </div>
      </main>
    </div>
  );
}
