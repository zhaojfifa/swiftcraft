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
  const [inputVideoUrl, setInputVideoUrl] = useState<string | null>(null);
  const [inputImageUrl, setInputImageUrl] = useState<string | null>(null);
  const [faceEnhancer, setFaceEnhancer] = useState(true);
  const [task, setTask] = useState<TaskRecord | null>(null);
  const [isRunning, setIsRunning] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [activeTab, setActiveTab] = useState<'playground' | 'json' | 'api'>('playground');
  const pollRef = useRef<NodeJS.Timeout | null>(null);

  useEffect(() => {
    return () => {
      if (pollRef.current) {
        clearInterval(pollRef.current);
      }
    };
  }, []);

  useEffect(() => {
    if (!videoFile) {
      setInputVideoUrl(null);
      return;
    }
    if (pollRef.current) {
      clearInterval(pollRef.current);
      pollRef.current = null;
    }
    setIsRunning(false);
    setTask(null);
    const previewUrl = URL.createObjectURL(videoFile);
    setInputVideoUrl(previewUrl);
    return () => {
      URL.revokeObjectURL(previewUrl);
    };
  }, [videoFile]);

  useEffect(() => {
    if (!imageFile) {
      setInputImageUrl(null);
      return;
    }
    const previewUrl = URL.createObjectURL(imageFile);
    setInputImageUrl(previewUrl);
    return () => {
      URL.revokeObjectURL(previewUrl);
    };
  }, [imageFile]);

  const handleRun = async () => {
    if (!videoFile || !imageFile) {
      setError('Please upload both a source video and a target image.');
      return;
    }
    setError(null);
    setIsRunning(true);
    setTask(null);

    try {
      const result = await createTask({
        videoFile,
        imageFile,
        mode,
        service: serviceType,
        faceEnhancer
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
          const done = status === 'done' || status === 'failed';
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
      }, 800);
    } catch (err) {
      setIsRunning(false);
      setError('Failed to start task.');
    }
  };

  const isDone = (task?.status || '').toLowerCase() === 'done';
  const outputUrl = isDone ? resolveAssetUrl(task?.output_url ?? null) : null;
  // Preview priority: output (done) -> local upload -> empty placeholder.
  const previewUrl = outputUrl ?? inputVideoUrl;
  const logs = task?.logs ?? [];
  const taskId = task?.task_id ?? task?.id ?? '';
  const canRun = Boolean(videoFile && imageFile) && !isRunning;
  const payloadPreview = {
    service: serviceType,
    mode,
    faceEnhancer,
    video: videoFile ? { name: videoFile.name, size: videoFile.size } : null,
    image: imageFile ? { name: imageFile.name, size: imageFile.size } : null
  };
  const jsonPreview = {
    request: payloadPreview,
    task: task ?? null
  };
  const apiBase = process.env.NEXT_PUBLIC_API_BASE || 'http://localhost:10000';
  const curlSnippet = [
    `curl -X POST \"${apiBase}/api/v1/tasks\"`,
    '  -F \"video_file=@<path/to/video.mp4>\"',
    '  -F \"image_file=@<path/to/image.jpg>\"',
    `  -F \"mode=${mode}\"`,
    `  -F \"service=${serviceType}\"`,
    `  -F \"face_enhancer=${faceEnhancer}\"`
  ].join(' \\\n');

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
            <button
              className={`pb-3 transition ${activeTab === 'playground' ? 'text-slate-900 border-b-2 border-slate-900 font-semibold' : 'text-slate-400 hover:text-slate-600'}`}
              onClick={() => setActiveTab('playground')}
            >
              Playground
            </button>
            <button
              className={`pb-3 transition ${activeTab === 'json' ? 'text-slate-900 border-b-2 border-slate-900 font-semibold' : 'text-slate-400 hover:text-slate-600'}`}
              onClick={() => setActiveTab('json')}
            >
              JSON
            </button>
            <button
              className={`pb-3 transition ${activeTab === 'api' ? 'text-slate-900 border-b-2 border-slate-900 font-semibold' : 'text-slate-400 hover:text-slate-600'}`}
              onClick={() => setActiveTab('api')}
            >
              API
            </button>
          </div>

          <div className="p-6 space-y-8 overflow-y-auto flex-1">
            {activeTab !== 'playground' ? (
              activeTab === 'json' ? (
                <div className="rounded-xl border border-slate-200 bg-white p-4 text-xs font-mono text-slate-700 whitespace-pre-wrap">
                  {JSON.stringify(jsonPreview, null, 2)}
                </div>
              ) : (
                <div className="space-y-3">
                  <div className="rounded-xl border border-slate-200 bg-white p-4 text-xs font-mono text-slate-700 whitespace-pre-wrap">
                    {curlSnippet}
                  </div>
                  <button
                    type="button"
                    onClick={() => navigator.clipboard.writeText(curlSnippet)}
                    className="inline-flex items-center rounded-lg border border-slate-200 bg-slate-50 px-3 py-1.5 text-xs font-medium text-slate-600 hover:bg-slate-100"
                  >
                    Copy curl
                  </button>
                </div>
              )
            ) : null}
            {activeTab === 'playground' ? (
              <div className="space-y-8">
                <div className="space-y-3">
                  <label className="text-xs font-bold text-slate-500 uppercase tracking-wider flex justify-between">
                    Source Video
                    <span className="text-[10px] font-normal text-slate-400">MP4, 4-8s</span>
                  </label>
                  <div className="flex items-center justify-between text-[11px] text-slate-400">
                    <span>{videoFile ? videoFile.name : 'No file selected'}</span>
                    {videoFile ? (
                      <button
                        type="button"
                        className="text-slate-500 hover:text-slate-700"
                        onClick={() => setVideoFile(null)}
                      >
                        Clear
                      </button>
                    ) : null}
                  </div>
                  <div className="group relative grid h-48 grid-rows-[1fr_auto] gap-3 rounded-xl border border-dashed border-slate-300 bg-slate-50 p-3 transition hover:bg-slate-100 hover:border-slate-400">
                    <div className="relative flex flex-col items-center justify-center">
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
                    <div className="rounded-lg border border-slate-200 bg-white p-2">
                      {inputVideoUrl ? (
                        <div className="flex items-center gap-3">
                          <video
                            src={inputVideoUrl}
                            muted
                            playsInline
                            className="h-10 w-14 rounded-md object-cover"
                          />
                          <span className="text-[11px] text-slate-500">Video preview ready</span>
                        </div>
                      ) : (
                        <div className="flex items-center justify-center text-[11px] text-slate-400">
                          Preview will appear here
                        </div>
                      )}
                    </div>
                  </div>
                </div>

                <div className="space-y-3">
                  <label className="text-xs font-bold text-slate-500 uppercase tracking-wider">
                    {serviceType === 'swap' ? 'Target Face' : 'Character Reference'}
                  </label>
                  <div className="flex items-center justify-between text-[11px] text-slate-400">
                    <span>{imageFile ? imageFile.name : 'No file selected'}</span>
                    {imageFile ? (
                      <button
                        type="button"
                        className="text-slate-500 hover:text-slate-700"
                        onClick={() => setImageFile(null)}
                      >
                        Clear
                      </button>
                    ) : null}
                  </div>
                  <div className="group relative grid h-48 grid-rows-[1fr_auto] gap-3 rounded-xl border border-dashed border-slate-300 bg-slate-50 p-3 transition hover:bg-slate-100 hover:border-slate-400">
                    <div className="relative flex flex-col items-center justify-center">
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
                    <div className="rounded-lg border border-slate-200 bg-white p-2">
                      {inputImageUrl ? (
                        <div className="flex items-center gap-3">
                          <img src={inputImageUrl} alt="Target preview" className="h-10 w-10 rounded-md object-cover" />
                          <span className="text-[11px] text-slate-500">Target preview ready</span>
                        </div>
                      ) : (
                        <div className="flex items-center justify-center text-[11px] text-slate-400">
                          Preview will appear here
                        </div>
                      )}
                    </div>
                  </div>
                </div>

                <div className="pt-6 border-t border-slate-100">
                  <div className="flex justify-between items-center py-2">
                    <span className="text-sm font-medium text-slate-700">Face Enhancer</span>
                    <button
                      type="button"
                      onClick={() => setFaceEnhancer((prev) => !prev)}
                      className={`w-10 h-6 rounded-full relative cursor-pointer shadow-inner ${faceEnhancer ? 'bg-blue-600' : 'bg-slate-300'}`}
                    >
                      <div className={`absolute top-1 w-4 h-4 bg-white rounded-full shadow-sm transition-all ${faceEnhancer ? 'right-1' : 'left-1'}`}></div>
                    </button>
                  </div>
                </div>
                {error ? <p className="text-xs text-rose-500">{error}</p> : null}
                {taskId ? (
                  <div className="rounded-lg border border-slate-200 bg-slate-50 p-3 text-xs text-slate-600">
                    <div>Task ID: {taskId}</div>
                    <div>Status: {task?.status || 'queued'} · Stage: {task?.stage || 'queued'}</div>
                  </div>
                ) : null}
              </div>
            ) : null}
          </div>

          <div className="p-6 border-t border-slate-100 bg-white">
            <button
              onClick={handleRun}
              disabled={!canRun}
              className={`w-full py-3.5 rounded-xl font-bold text-white flex items-center justify-center gap-2 shadow-lg hover:shadow-xl hover:-translate-y-0.5 transition-all ${
                mode === 'intelligent' ? 'bg-blue-600 hover:bg-blue-700 shadow-blue-200' : 'bg-slate-800 hover:bg-slate-900 shadow-slate-200'
              } ${!canRun ? 'opacity-60 cursor-not-allowed' : ''}`}
            >
              <Play className="w-4 h-4" />
              {isRunning ? 'Running...' : `Run ${mode === 'intelligent' ? 'SwiftFlow' : 'Basic'}`}
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
            {previewUrl ? (
              <video controls className="w-full h-full object-cover" src={previewUrl} />
            ) : (
              <div className="text-slate-500 font-medium flex flex-col items-center gap-3">
                <div className="w-16 h-16 rounded-full bg-slate-800/50 flex items-center justify-center backdrop-blur">
                  <Play className="w-6 h-6 text-slate-400 ml-1" />
                </div>
                Output Preview
              </div>
            )}
          </div>

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
        </div>
      </div>
    </div>
  );
}
