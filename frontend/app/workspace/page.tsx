'use client';

import { useEffect, useRef, useState } from 'react';
import { useSearchParams } from 'next/navigation';
import { UploadCloud, Play, Terminal } from 'lucide-react';
import Link from 'next/link';

import { createTask, getTask, TaskRecord } from '../../lib/api';
import { resolveAssetUrl } from '../../lib/url';

export default function Workspace() {
  const searchParams = useSearchParams();
  const serviceType = searchParams.get('service') || 'swap';

  const [mode, setMode] = useState<'baseline' | 'intelligent'>('intelligent');
  const [videoFile, setVideoFile] = useState<File | null>(null);
  const [imageFile, setImageFile] = useState<File | null>(null);
  const [task, setTask] = useState<TaskRecord | null>(null);
  const [isRunning, setIsRunning] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const pollRef = useRef<NodeJS.Timeout | null>(null);

  useEffect(() => {
    return () => {
      if (pollRef.current) {
        clearInterval(pollRef.current);
      }
    };
  }, []);

  const handleRun = async () => {
    if (!videoFile || !imageFile) {
      setError('Please upload both a source video and a target image.');
      return;
    }
    setError(null);
    setIsRunning(true);

    try {
      const result = await createTask({
        videoFile,
        imageFile,
        mode,
        service: serviceType
      });
      const taskId = result.task_id;
      let ticks = 0;

      if (pollRef.current) {
        clearInterval(pollRef.current);
      }

      pollRef.current = setInterval(async () => {
        ticks += 1;
        try {
          const latest = await getTask(taskId);
          setTask(latest);
          const status = (latest.status || '').toLowerCase();
          const stage = (latest.stage || '').toLowerCase();
          const done = status === 'succeeded' || status === 'completed' || status === 'failed' || stage === 'completed' || stage === 'failed';
          if (done || ticks >= 60) {
            if (pollRef.current) {
              clearInterval(pollRef.current);
            }
            setIsRunning(false);
          }
        } catch (err) {
          if (pollRef.current) {
            clearInterval(pollRef.current);
          }
          setIsRunning(false);
          setError('Polling failed. Is the backend running?');
        }
      }, 1000);
    } catch (err) {
      setIsRunning(false);
      setError('Failed to start task.');
    }
  };

  const outputUrl = resolveAssetUrl(task?.output_url ?? task?.result_url ?? null);
  const logs = task?.logs ?? [];

  return (
    <div className="h-screen bg-white text-slate-900 flex flex-col font-sans overflow-hidden">
      <nav className="h-16 bg-white border-b border-slate-200 flex items-center justify-between px-6 shrink-0 z-20 shadow-sm">
        <div className="flex items-center gap-4">
          <Link href="/" className="font-bold text-slate-900 tracking-tight hover:text-blue-600 transition">SwiftCraft</Link>
          <span className="text-slate-300">/</span>
          <span className="font-medium text-slate-600 capitalize">{serviceType}</span>
        </div>

        <div className="bg-slate-100 p-1 rounded-lg border border-slate-200 flex relative">
          <button
            onClick={() => setMode('baseline')}
            className={`px-6 py-1.5 rounded-md text-sm font-medium transition-all duration-200 z-10 ${
              mode === 'baseline'
                ? 'bg-white text-slate-900 shadow-sm border border-slate-200 ring-1 ring-black/5'
                : 'text-slate-500 hover:text-slate-700'
            }`}
          >
            Baseline
          </button>
          <button
            onClick={() => setMode('intelligent')}
            className={`px-6 py-1.5 rounded-md text-sm font-medium transition-all duration-200 z-10 ${
              mode === 'intelligent'
                ? 'bg-white text-blue-600 shadow-sm border border-slate-200 ring-1 ring-black/5'
                : 'text-slate-500 hover:text-slate-700'
            }`}
          >
            Intelligent
          </button>
        </div>

        <div className="text-xs font-medium text-slate-500 bg-slate-50 px-2 py-1 rounded border border-slate-200">
          v1.6 Demo
        </div>
      </nav>

      <div className="flex-1 flex overflow-hidden">
        <div className="w-[400px] bg-white border-r border-slate-200 flex flex-col z-10 shadow-[4px_0_24px_rgba(0,0,0,0.02)]">
          <div className="flex border-b border-slate-100 px-6 pt-6 gap-6 text-sm">
            <button className="text-slate-900 border-b-2 border-slate-900 pb-3 font-semibold">Playground</button>
            <button className="text-slate-400 pb-3 hover:text-slate-600 transition">JSON</button>
            <button className="text-slate-400 pb-3 hover:text-slate-600 transition">API</button>
          </div>

          <div className="p-6 space-y-8 overflow-y-auto flex-1">
            <div className="space-y-3">
              <label className="text-xs font-bold text-slate-500 uppercase tracking-wider flex justify-between">
                Source Video
                <span className="text-[10px] font-normal text-slate-400">MP4, 4-8s</span>
              </label>
              <div className="relative border border-dashed border-slate-300 rounded-xl h-36 flex flex-col items-center justify-center bg-slate-50 hover:bg-slate-100 hover:border-slate-400 transition cursor-pointer group">
                <input
                  type="file"
                  accept="video/*"
                  onChange={(event) => setVideoFile(event.target.files?.[0] || null)}
                  className="absolute inset-0 opacity-0 cursor-pointer"
                />
                <div className="p-3 bg-white rounded-full shadow-sm mb-3 group-hover:scale-110 transition-transform border border-slate-100">
                  <UploadCloud className="w-5 h-5 text-slate-600" />
                </div>
                <span className="text-xs font-medium text-slate-600">Click to upload video</span>
                <span className="text-[10px] text-slate-400 mt-1">or drag and drop</span>
              </div>
            </div>

            <div className="space-y-3">
              <label className="text-xs font-bold text-slate-500 uppercase tracking-wider">
                {serviceType === 'swap' ? 'Target Face' : 'Character Reference'}
              </label>
              <div className="relative border border-dashed border-slate-300 rounded-xl h-36 flex flex-col items-center justify-center bg-slate-50 hover:bg-slate-100 hover:border-slate-400 transition cursor-pointer group">
                <input
                  type="file"
                  accept="image/*"
                  onChange={(event) => setImageFile(event.target.files?.[0] || null)}
                  className="absolute inset-0 opacity-0 cursor-pointer"
                />
                <div className="p-3 bg-white rounded-full shadow-sm mb-3 group-hover:scale-110 transition-transform border border-slate-100">
                  <UploadCloud className="w-5 h-5 text-slate-600" />
                </div>
                <span className="text-xs font-medium text-slate-600">Click to upload image</span>
              </div>
            </div>

            <div className="pt-6 border-t border-slate-100">
              <div className="flex justify-between items-center py-2">
                <span className="text-sm font-medium text-slate-700">Face Enhancer</span>
                <div className="w-10 h-6 bg-blue-600 rounded-full relative cursor-pointer shadow-inner">
                  <div className="absolute right-1 top-1 w-4 h-4 bg-white rounded-full shadow-sm"></div>
                </div>
              </div>
            </div>
            {error ? <p className="text-xs text-rose-500">{error}</p> : null}
          </div>

          <div className="p-6 border-t border-slate-100 bg-white">
            <button
              onClick={handleRun}
              disabled={isRunning}
              className={`w-full py-3.5 rounded-xl font-bold text-white flex items-center justify-center gap-2 shadow-lg hover:shadow-xl hover:-translate-y-0.5 transition-all ${
                mode === 'intelligent' ? 'bg-blue-600 hover:bg-blue-700 shadow-blue-200' : 'bg-slate-800 hover:bg-slate-900 shadow-slate-200'
              } ${isRunning ? 'opacity-60 cursor-not-allowed' : ''}`}
            >
              <Play className="w-4 h-4" />
              Run {mode === 'intelligent' ? 'SwiftFlow' : 'Basic'}
            </button>
            <div className="text-center mt-3 text-[10px] text-slate-400 font-medium">
              Estimated Cost: {mode === 'intelligent' ? '$0.15' : '$0.05'}
            </div>
          </div>
        </div>

        <div className="flex-1 bg-slate-50/80 p-10 flex flex-col items-center justify-center relative">
          <div
            className="absolute inset-0 opacity-[0.05] pointer-events-none"
            style={{ backgroundImage: 'radial-gradient(#475569 1px, transparent 1px)', backgroundSize: '24px 24px' }}
          ></div>

          <div className="w-full max-w-4xl aspect-video bg-black rounded-2xl shadow-2xl border border-slate-300/50 flex flex-col items-center justify-center relative overflow-hidden group">
            {outputUrl ? (
              <video controls className="w-full h-full object-cover" src={outputUrl} />
            ) : (
              <div className="text-slate-500 font-medium flex flex-col items-center gap-3">
                <div className="w-16 h-16 rounded-full bg-slate-800/50 flex items-center justify-center backdrop-blur">
                  <Play className="w-6 h-6 text-slate-400 ml-1" />
                </div>
                Output Preview
              </div>
            )}
          </div>

          {mode === 'intelligent' && (
            <div className="w-full max-w-4xl mt-6 animate-in slide-in-from-bottom-4 fade-in duration-500">
              <div className="flex items-center gap-2 mb-2 ml-1">
                <Terminal className="w-3 h-3 text-slate-400" />
                <span className="text-xs font-semibold text-slate-500 uppercase tracking-wider">SwiftFlow Engine Logs</span>
              </div>
              <div className="bg-white border border-slate-200 rounded-lg p-4 shadow-sm font-mono text-xs h-32 overflow-y-auto">
                {logs.length ? (
                  logs.map((line, index) => (
                    <div key={`${line}-${index}`} className="flex gap-3 py-1 border-b border-slate-50">
                      <span className="text-slate-400 w-12 select-none">[{String(index + 1).padStart(2, '0')}]</span>
                      <span className="text-slate-700">{line}</span>
                    </div>
                  ))
                ) : (
                  <div className="flex gap-3 py-1">
                    <span className="text-slate-400 w-12 select-none">[--]</span>
                    <span className="text-slate-500 italic">Waiting for logs...</span>
                  </div>
                )}
              </div>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
