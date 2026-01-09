'use client';

import { useEffect, useMemo, useRef, useState } from 'react';
import Link from 'next/link';
import { createTask, getTask, TaskRecord } from '../../../lib/api';
import { PRESETS, SwapMode } from '../../../lib/presets';

type UiState =
  | { kind: 'idle' }
  | { kind: 'creating' }
  | { kind: 'polling'; taskId: string }
  | { kind: 'done'; task: TaskRecord }
  | { kind: 'error'; message: string; taskId?: string };

export default function SwapWorkspacePage() {
  const [mode, setMode] = useState<SwapMode>('baseline');
  const [state, setState] = useState<UiState>({ kind: 'idle' });
  const [task, setTask] = useState<TaskRecord | null>(null);

  const pollTimer = useRef<number | null>(null);

  const inputKey = useMemo(() => PRESETS.swap[mode], [mode]);

  useEffect(() => {
    return () => {
      if (pollTimer.current) window.clearInterval(pollTimer.current);
    };
  }, []);

  async function run() {
    try {
      setTask(null);
      setState({ kind: 'creating' });

      const resp = await createTask({
        service: 'swap',
        mode,
        input_key: inputKey,
      });

      const taskId = resp.task_id;
      setState({ kind: 'polling', taskId });

      if (pollTimer.current) window.clearInterval(pollTimer.current);
      pollTimer.current = window.setInterval(async () => {
        try {
          const t = await getTask(taskId);
          setTask(t);

          const s = (t.status || '').toString().toLowerCase();
          if (s === 'done') {
            if (pollTimer.current) window.clearInterval(pollTimer.current);
            setState({ kind: 'done', task: t });
          } else if (s === 'failed') {
            if (pollTimer.current) window.clearInterval(pollTimer.current);
            setState({ kind: 'error', message: t.error || 'Task failed', taskId });
          }
        } catch (e: any) {
          if (pollTimer.current) window.clearInterval(pollTimer.current);
          setState({ kind: 'error', message: e?.message || 'Polling error', taskId });
        }
      }, 1200);
    } catch (e: any) {
      setState({ kind: 'error', message: e?.message || 'Create task error' });
    }
  }

  const taskId =
    state.kind === 'polling' ? state.taskId :
    state.kind === 'error' ? state.taskId :
    state.kind === 'done' ? state.task.task_id :
    undefined;

  const outputUrl = (state.kind === 'done' ? state.task.output_url : task?.output_url) || null;

  return (
    <main style={{ padding: 24, maxWidth: 1100, margin: '0 auto' }}>
      <header style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 12, marginBottom: 18 }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
          <div style={{
            width: 34, height: 34, borderRadius: 10,
            background: '#2563eb', display: 'inline-flex',
            alignItems: 'center', justifyContent: 'center',
            color: 'white', fontWeight: 700
          }}>
            ✦
          </div>
          <div>
            <div style={{ fontSize: 18, fontWeight: 800 }}>Swap Workspace</div>
            <div style={{ color: '#6b7280', fontSize: 13 }}>Mock output to R2 → CDN playback</div>
          </div>
        </div>
        <Link href="/" style={{ color: '#2563eb', fontWeight: 700, textDecoration: 'none' }}>
          ← Back
        </Link>
      </header>

      <section style={{ border: '1px solid #e5e7eb', borderRadius: 16, padding: 18 }}>
        <div style={{ display: 'flex', gap: 12, alignItems: 'center', flexWrap: 'wrap' }}>
          <label style={{ fontWeight: 700 }}>Mode</label>
          <select
            value={mode}
            onChange={(e) => setMode(e.target.value as SwapMode)}
            style={{ padding: '8px 10px', borderRadius: 10, border: '1px solid #e5e7eb' }}
            disabled={state.kind === 'creating' || state.kind === 'polling'}
          >
            <option value="baseline">baseline</option>
            <option value="intelligent">intelligent</option>
          </select>

          <div style={{ color: '#6b7280', fontSize: 13 }}>
            input_key: <code>{inputKey}</code>
          </div>

          <button
            onClick={run}
            disabled={state.kind === 'creating' || state.kind === 'polling'}
            style={{
              marginLeft: 'auto',
              padding: '10px 14px',
              borderRadius: 12,
              border: '1px solid #2563eb',
              background: '#2563eb',
              color: 'white',
              fontWeight: 800,
              cursor: 'pointer',
              opacity: (state.kind === 'creating' || state.kind === 'polling') ? 0.6 : 1
            }}
          >
            {state.kind === 'creating' ? 'Creating…' : state.kind === 'polling' ? 'Running…' : 'Run'}
          </button>
        </div>

        <div style={{ marginTop: 14, display: 'grid', gridTemplateColumns: '1fr', gap: 10 }}>
          <div style={{ padding: 12, borderRadius: 12, background: '#f9fafb', border: '1px solid #eef2f7' }}>
            <div style={{ fontWeight: 800, marginBottom: 6 }}>Status</div>
            <div style={{ color: '#374151' }}>
              {state.kind === 'idle' && 'Ready.'}
              {state.kind === 'creating' && 'Creating task…'}
              {state.kind === 'polling' && `Polling… task_id=${state.taskId}`}
              {state.kind === 'done' && `Done. task_id=${state.task.task_id}`}
              {state.kind === 'error' && `Error: ${state.message}`}
            </div>
            {taskId && (
              <div style={{ marginTop: 8, color: '#6b7280', fontSize: 13 }}>
                Task ID: <code>{taskId}</code>
              </div>
            )}
            {task?.status && (
              <div style={{ marginTop: 6, color: '#6b7280', fontSize: 13 }}>
                Latest: status=<code>{String(task.status)}</code>{' '}
                {typeof task.progress === 'number' ? <>progress=<code>{task.progress}</code></> : null}
              </div>
            )}
          </div>

          <div style={{ padding: 12, borderRadius: 12, border: '1px solid #e5e7eb' }}>
            <div style={{ fontWeight: 800, marginBottom: 10 }}>Result</div>

            {!outputUrl && (
              <div style={{ color: '#6b7280' }}>No output_url yet.</div>
            )}

            {outputUrl && (
              <div style={{ display: 'grid', gap: 10 }}>
                <video src={outputUrl} controls style={{ width: '100%', borderRadius: 12, border: '1px solid #e5e7eb' }} />
                <div style={{ display: 'flex', gap: 12, flexWrap: 'wrap' }}>
                  <a href={outputUrl} target="_blank" rel="noreferrer" style={{ color: '#2563eb', fontWeight: 800 }}>
                    Open result in new tab
                  </a>
                  <span style={{ color: '#6b7280' }}>
                    (Use this exact URL; do not use <code>&lt;task_id&gt;</code> placeholders.)
                  </span>
                </div>
              </div>
            )}
          </div>

          {task?.logs?.length ? (
            <div style={{ padding: 12, borderRadius: 12, border: '1px solid #e5e7eb' }}>
              <div style={{ fontWeight: 800, marginBottom: 8 }}>Logs</div>
              <pre style={{ margin: 0, whiteSpace: 'pre-wrap', color: '#374151' }}>
                {task.logs.join('\n')}
              </pre>
            </div>
          ) : null}
        </div>
      </section>
    </main>
  );
}
