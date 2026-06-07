import { useState, useMemo, useCallback, useEffect } from 'react';
import axios from 'axios';
import cytoscape from 'cytoscape';
import CytoscapeComponent from 'react-cytoscapejs';
import dagre from 'cytoscape-dagre';
import fcose from 'cytoscape-fcose';
import { Search, Loader2, AlertCircle, Info, DollarSign, Clock, Shield, Moon, Sun, RefreshCw, FileText, Cpu } from 'lucide-react';
import type { TraceEvent, TraceResponse, PolicyResponse } from './types';

cytoscape.use(dagre);
cytoscape.use(fcose);

const API_URL = import.meta.env.VITE_API_URL || 'http://localhost:8000';

function App() {
  const [activeTab, setActiveTab] = useState<'trace' | 'policy'>('trace');
  const [runId, setRunId] = useState('');
  const [jwtToken, setJwtToken] = useState('');
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [traceData, setTraceData] = useState<TraceResponse | null>(null);
  const [elements, setElements] = useState<any[]>([]);
  const [selectedEvent, setSelectedEvent] = useState<TraceEvent | null>(null);
  const [cyInstance, setCyInstance] = useState<cytoscape.Core | null>(null);
  const [isDarkMode, setIsDarkMode] = useState(false);

  // Policy tab state
  const [policyData, setPolicyData] = useState<PolicyResponse | null>(null);
  const [policyLoading, setPolicyLoading] = useState(false);
  const [policyError, setPolicyError] = useState<string | null>(null);
  const [reloadStatus, setReloadStatus] = useState<string | null>(null);

  useEffect(() => {
    if (isDarkMode) {
      document.documentElement.classList.add('dark');
    } else {
      document.documentElement.classList.remove('dark');
    }
  }, [isDarkMode]);

  const fetchTrace = async (id: string, token: string) => {
    if (!id) return;
    setLoading(true);
    setError(null);
    setSelectedEvent(null);
    try {
      const response = await axios.get(`${API_URL}/runs/${id}/trace`, {
        headers: {
            'Authorization': `Bearer ${token}`
        }
      });
      const data = response.data as TraceResponse;
      setTraceData(data);

      const newElements: any[] = [];
      data.events.forEach((event) => {
        let label = event.node;
        if (event.agent_id) {
            label += ` (${event.agent_id})`;
        } else if (event.action) {
            label += `\n${event.action}`;
        }

        let bgColor = isDarkMode ? '#334155' : '#e2e8f0'; // default gray
        let borderColor = isDarkMode ? '#475569' : '#94a3b8';

        if (event.status === 'error' || event.status === 'blocked') {
            bgColor = isDarkMode ? '#7f1d1d' : '#fecaca'; // red
            borderColor = isDarkMode ? '#ef4444' : '#ef4444';
        } else if (event.status === 'hitl_pending') {
            bgColor = isDarkMode ? '#854d0e' : '#fef08a'; // yellow
            borderColor = isDarkMode ? '#eab308' : '#eab308';
        } else if (event.status === 'ok') {
            bgColor = isDarkMode ? '#14532d' : '#bbf7d0'; // green
            borderColor = isDarkMode ? '#22c55e' : '#22c55e';
        }

        newElements.push({
          data: {
            id: event.event_id,
            label: label,
            eventData: event,
            bgColor,
            borderColor,
          }
        });
      });

      data.edges.forEach((edge) => {
        newElements.push({
          data: {
            source: edge.from,
            target: edge.to,
            id: `${edge.from}-${edge.to}`
          }
        });
      });

      setElements(newElements);
    } catch (err: any) {
      console.error(err);
      setError(err.response?.data?.detail || 'Failed to fetch trace. Please ensure the backend is running, the Run ID is valid, and the token is correct.');
      setTraceData(null);
      setElements([]);
    } finally {
      setLoading(false);
    }
  };

  const handleSearch = (e: React.FormEvent) => {
    e.preventDefault();
    fetchTrace(runId, jwtToken);
  };

  const fetchPolicy = useCallback(async () => {
    setPolicyLoading(true);
    setPolicyError(null);
    setReloadStatus(null);
    try {
      const response = await axios.get(`${API_URL}/policy`, {
        headers: jwtToken ? { 'Authorization': `Bearer ${jwtToken}` } : {},
      });
      setPolicyData(response.data as PolicyResponse);
    } catch (err: any) {
      setPolicyError(err.response?.data?.detail || 'Failed to fetch policy.');
    } finally {
      setPolicyLoading(false);
    }
  }, [jwtToken]);

  const reloadPolicy = useCallback(async () => {
    setPolicyLoading(true);
    setReloadStatus(null);
    setPolicyError(null);
    try {
      const response = await axios.post(`${API_URL}/policy/reload`, {}, {
        headers: jwtToken ? { 'Authorization': `Bearer ${jwtToken}` } : {},
      });
      setReloadStatus(`Reloaded — ${response.data.rules_count} rules active.`);
      await fetchPolicy();
    } catch (err: any) {
      setPolicyError(err.response?.data?.detail || 'Failed to reload policy.');
    } finally {
      setPolicyLoading(false);
    }
  }, [jwtToken, fetchPolicy]);

  useEffect(() => {
    if (activeTab === 'policy' && !policyData && !policyLoading) {
      fetchPolicy();
    }
  }, [activeTab, policyData, policyLoading, fetchPolicy]);

  const layout = useMemo(() => ({
    name: 'dagre',
    rankDir: 'TB',
    padding: 50,
    fit: true,
    spacingFactor: 1.2,
    nodeSep: 60,
    rankSep: 100,
    animate: true,
    animationDuration: 500,
  }), []);

  const stylesheet = useMemo<cytoscape.StylesheetStyle[]>(() => [
    {
      selector: 'node',
      style: {
        'label': 'data(label)',
        'text-wrap': 'wrap',
        'text-max-width': '150px',
        'text-valign': 'center',
        'text-halign': 'center',
        'font-size': '10px',
        'font-family': 'ui-sans-serif, system-ui, sans-serif',
        'width': '160px',
        'height': '60px',
        'shape': 'round-rectangle',
        'background-color': 'data(bgColor)',
        'border-width': 2,
        'border-color': 'data(borderColor)',
        'color': isDarkMode ? '#f8fafc' : '#1e293b',
        'transition-property': 'background-color, border-color, border-width, width, height',
        'transition-duration': 200,
      } as any,
    },
    {
      selector: 'node:selected',
      style: {
        'border-width': 4,
        'border-color': '#3b82f6',
        'shadow-blur': 10,
        'shadow-color': '#3b82f6',
        'shadow-opacity': 0.5,
      } as any,
    },
    {
      selector: 'edge',
      style: {
        'width': 2,
        'line-color': isDarkMode ? '#475569' : '#cbd5e1',
        'target-arrow-color': isDarkMode ? '#475569' : '#cbd5e1',
        'target-arrow-shape': 'triangle',
        'curve-style': 'bezier',
      }
    }
  ], [isDarkMode]);

  // Update elements when theme changes so cytoscape re-renders with new colors
  useEffect(() => {
     setElements(prev => prev.map(el => {
         if (el.data.eventData) {
            const event = el.data.eventData;
            let bgColor = isDarkMode ? '#334155' : '#e2e8f0';
            let borderColor = isDarkMode ? '#475569' : '#94a3b8';
            if (event.status === 'error' || event.status === 'blocked') {
                bgColor = isDarkMode ? '#7f1d1d' : '#fecaca';
                borderColor = isDarkMode ? '#ef4444' : '#ef4444';
            } else if (event.status === 'hitl_pending') {
                bgColor = isDarkMode ? '#854d0e' : '#fef08a';
                borderColor = isDarkMode ? '#eab308' : '#eab308';
            } else if (event.status === 'ok') {
                bgColor = isDarkMode ? '#14532d' : '#bbf7d0';
                borderColor = isDarkMode ? '#22c55e' : '#22c55e';
            }
            return {
                ...el,
                data: {
                    ...el.data,
                    bgColor,
                    borderColor
                }
            };
         }
         return el;
     }));
  }, [isDarkMode]);

  const handleNodeClick = useCallback((event: cytoscape.EventObject) => {
    const node = event.target;
    if (node.isNode()) {
      setSelectedEvent(node.data('eventData'));
    }
  }, []);

  const handleBackgroundClick = useCallback((event: cytoscape.EventObject) => {
    if (event.target === event.cy) {
      setSelectedEvent(null);
    }
  }, []);

  return (
    <div className={`flex h-screen w-full flex-col overflow-hidden ${isDarkMode ? 'dark bg-slate-950 text-slate-50' : 'bg-slate-50 text-slate-900'}`}>
      {/* Header */}
      <header className={`border-b p-4 shrink-0 flex flex-col sm:flex-row items-center justify-between z-10 gap-4 ${isDarkMode ? 'bg-slate-900 border-slate-800' : 'bg-white border-slate-200'}`}>
        <div className="flex items-center gap-4 shrink-0">
          <div className="flex items-center space-x-2">
            <Shield className="h-6 w-6 text-blue-600" />
            <h1 className="text-xl font-semibold">AgentGuard</h1>
          </div>
          {/* Tab switcher */}
          <div className={`flex rounded-md border overflow-hidden text-sm ${isDarkMode ? 'border-slate-700' : 'border-slate-200'}`}>
            <button
              onClick={() => setActiveTab('trace')}
              className={`flex items-center gap-1.5 px-3 py-1.5 ${activeTab === 'trace'
                ? 'bg-blue-600 text-white'
                : isDarkMode ? 'bg-slate-800 text-slate-300 hover:bg-slate-700' : 'bg-white text-slate-600 hover:bg-slate-50'}`}
            >
              <Search className="h-3.5 w-3.5" />
              Trace Viewer
            </button>
            <button
              onClick={() => setActiveTab('policy')}
              className={`flex items-center gap-1.5 px-3 py-1.5 border-l ${isDarkMode ? 'border-slate-700' : 'border-slate-200'} ${activeTab === 'policy'
                ? 'bg-blue-600 text-white'
                : isDarkMode ? 'bg-slate-800 text-slate-300 hover:bg-slate-700' : 'bg-white text-slate-600 hover:bg-slate-50'}`}
            >
              <FileText className="h-3.5 w-3.5" />
              Policy
            </button>
          </div>
        </div>

        <div className="flex items-center gap-2 w-full sm:w-auto">
          {activeTab === 'trace' && (
            <form onSubmit={handleSearch} className="flex flex-col sm:flex-row w-full sm:max-w-2xl gap-2">
              <div className="flex flex-col sm:flex-row flex-grow gap-2">
                <input
                  type="text"
                  value={jwtToken}
                  onChange={(e) => setJwtToken(e.target.value)}
                  placeholder="Bearer Token (Optional)"
                  className={`block w-full sm:w-1/3 px-3 py-2 border rounded-md leading-5 focus:outline-none focus:ring-1 focus:ring-blue-500 focus:border-blue-500 sm:text-sm ${isDarkMode ? 'bg-slate-800 border-slate-700 placeholder-slate-400 text-slate-100' : 'bg-white border-slate-300 placeholder-slate-500 text-slate-900'}`}
                />
                <div className="relative flex-grow">
                  <div className="absolute inset-y-0 left-0 pl-3 flex items-center pointer-events-none">
                    <Search className="h-4 w-4 text-slate-400" />
                  </div>
                  <input
                    type="text"
                    value={runId}
                    onChange={(e) => setRunId(e.target.value)}
                    placeholder="Enter Run ID..."
                    className={`block w-full pl-10 pr-3 py-2 border rounded-md leading-5 focus:outline-none focus:ring-1 focus:ring-blue-500 focus:border-blue-500 sm:text-sm ${isDarkMode ? 'bg-slate-800 border-slate-700 placeholder-slate-400 text-slate-100' : 'bg-white border-slate-300 placeholder-slate-500 text-slate-900'}`}
                  />
                </div>
              </div>
              <div className="flex gap-2">
                <button
                  type="submit"
                  disabled={loading || !runId.trim()}
                  className="flex items-center justify-center px-4 py-2 border border-transparent text-sm font-medium rounded-md text-white bg-blue-600 hover:bg-blue-700 focus:outline-none focus:ring-2 focus:ring-offset-2 focus:ring-blue-500 disabled:opacity-50 disabled:cursor-not-allowed whitespace-nowrap"
                >
                  {loading ? <Loader2 className="h-4 w-4 animate-spin" /> : 'Load Trace'}
                </button>
              </div>
            </form>
          )}

          {activeTab === 'policy' && (
            <div className="flex items-center gap-2">
              <input
                type="text"
                value={jwtToken}
                onChange={(e) => setJwtToken(e.target.value)}
                placeholder="Bearer Token (Optional)"
                className={`block w-48 px-3 py-2 border rounded-md leading-5 focus:outline-none focus:ring-1 focus:ring-blue-500 focus:border-blue-500 sm:text-sm ${isDarkMode ? 'bg-slate-800 border-slate-700 placeholder-slate-400 text-slate-100' : 'bg-white border-slate-300 placeholder-slate-500 text-slate-900'}`}
              />
              <button
                onClick={reloadPolicy}
                disabled={policyLoading}
                className="flex items-center gap-1.5 px-4 py-2 border border-transparent text-sm font-medium rounded-md text-white bg-blue-600 hover:bg-blue-700 disabled:opacity-50 disabled:cursor-not-allowed whitespace-nowrap"
              >
                {policyLoading ? <Loader2 className="h-4 w-4 animate-spin" /> : <RefreshCw className="h-4 w-4" />}
                Reload Policy
              </button>
              <button
                onClick={fetchPolicy}
                disabled={policyLoading}
                className={`flex items-center gap-1.5 px-3 py-2 border text-sm rounded-md disabled:opacity-50 ${isDarkMode ? 'border-slate-700 bg-slate-800 text-slate-300 hover:bg-slate-700' : 'border-slate-300 bg-white text-slate-600 hover:bg-slate-50'}`}
              >
                <RefreshCw className="h-3.5 w-3.5" />
                Refresh
              </button>
            </div>
          )}

          <button
            type="button"
            onClick={() => setIsDarkMode(!isDarkMode)}
            className={`p-2 rounded-md border flex items-center justify-center shrink-0 ${isDarkMode ? 'bg-slate-800 border-slate-700 text-yellow-400 hover:bg-slate-700' : 'bg-white border-slate-300 text-slate-600 hover:bg-slate-50'}`}
            title={isDarkMode ? "Switch to Light Mode" : "Switch to Dark Mode"}
          >
            {isDarkMode ? <Sun className="h-5 w-5" /> : <Moon className="h-5 w-5" />}
          </button>
        </div>
      </header>

      {/* Main Content */}
      <div className="flex flex-1 overflow-hidden">

        {/* ── Policy Tab ────────────────────────────────────────────────── */}
        {activeTab === 'policy' && (
          <div className="flex-1 overflow-y-auto p-6">
            {policyError && (
              <div className={`mb-4 px-4 py-3 rounded-md flex items-center space-x-2 border ${isDarkMode ? 'bg-red-950 border-red-900 text-red-200' : 'bg-red-50 border-red-200 text-red-700'}`}>
                <AlertCircle className="h-5 w-5 flex-shrink-0" />
                <p className="text-sm">{policyError}</p>
              </div>
            )}
            {reloadStatus && (
              <div className={`mb-4 px-4 py-3 rounded-md flex items-center space-x-2 border ${isDarkMode ? 'bg-green-950 border-green-900 text-green-200' : 'bg-green-50 border-green-200 text-green-700'}`}>
                <Info className="h-5 w-5 flex-shrink-0" />
                <p className="text-sm">{reloadStatus}</p>
              </div>
            )}
            {policyLoading && !policyData && (
              <div className="flex items-center justify-center h-32 text-slate-400">
                <Loader2 className="h-8 w-8 animate-spin" />
              </div>
            )}
            {!policyData && !policyLoading && !policyError && (
              <div className="flex flex-col items-center justify-center h-32 text-slate-400">
                <FileText className="h-10 w-10 mb-2 opacity-50" />
                <p className="text-sm">No policy loaded. Add a Bearer Token and click Refresh.</p>
              </div>
            )}
            {policyData && (
              <div className="space-y-4 max-w-3xl">
                <div className={`flex items-center justify-between pb-3 border-b ${isDarkMode ? 'border-slate-700' : 'border-slate-200'}`}>
                  <div>
                    <h2 className="text-base font-semibold">Active Policy</h2>
                    <p className={`text-xs mt-0.5 ${isDarkMode ? 'text-slate-400' : 'text-slate-500'}`}>
                      version {policyData.policy_version} — read-only view
                    </p>
                  </div>
                </div>

                {/* Budget section */}
                {policyData.content.budget && (
                  <div className={`rounded-lg border p-4 ${isDarkMode ? 'bg-slate-900 border-slate-700' : 'bg-white border-slate-200'}`}>
                    <h3 className={`text-sm font-medium mb-3 flex items-center gap-1.5 ${isDarkMode ? 'text-slate-200' : 'text-slate-800'}`}>
                      <DollarSign className="h-4 w-4 text-blue-500" />
                      Budget
                    </h3>
                    <div className="grid grid-cols-2 gap-3">
                      {Object.entries(policyData.content.budget).map(([k, v]) => (
                        <div key={k} className={`p-2 rounded border ${isDarkMode ? 'bg-slate-800 border-slate-700' : 'bg-slate-50 border-slate-100'}`}>
                          <span className={`text-xs block mb-0.5 ${isDarkMode ? 'text-slate-400' : 'text-slate-500'}`}>{k}</span>
                          <span className="text-sm font-mono font-semibold">{String(v)}</span>
                        </div>
                      ))}
                    </div>
                  </div>
                )}

                {/* Content rules */}
                {policyData.content.content_rules && (policyData.content.content_rules as any[]).length > 0 && (
                  <div className={`rounded-lg border p-4 ${isDarkMode ? 'bg-slate-900 border-slate-700' : 'bg-white border-slate-200'}`}>
                    <h3 className={`text-sm font-medium mb-3 ${isDarkMode ? 'text-slate-200' : 'text-slate-800'}`}>
                      Content Rules ({(policyData.content.content_rules as any[]).length})
                    </h3>
                    <div className="space-y-2">
                      {(policyData.content.content_rules as any[]).map((rule: any) => (
                        <div key={rule.id} className={`p-3 rounded-md border text-sm ${
                          rule.action === 'block'
                            ? (isDarkMode ? 'bg-red-950 border-red-900' : 'bg-red-50 border-red-200')
                            : rule.action === 'require_hitl'
                            ? (isDarkMode ? 'bg-yellow-950 border-yellow-900' : 'bg-yellow-50 border-yellow-200')
                            : (isDarkMode ? 'bg-slate-800 border-slate-700' : 'bg-slate-50 border-slate-100')
                        }`}>
                          <div className="flex items-center justify-between mb-1">
                            <span className="font-mono font-medium">{rule.id}</span>
                            <span className={`text-xs px-2 py-0.5 rounded border font-medium ${
                              rule.action === 'block'
                                ? (isDarkMode ? 'bg-red-900 text-red-200 border-red-800' : 'bg-red-100 text-red-700 border-red-200')
                                : rule.action === 'require_hitl'
                                ? (isDarkMode ? 'bg-yellow-900 text-yellow-200 border-yellow-800' : 'bg-yellow-100 text-yellow-700 border-yellow-200')
                                : (isDarkMode ? 'bg-green-900 text-green-200 border-green-800' : 'bg-green-100 text-green-700 border-green-200')
                            }`}>{rule.action}</span>
                          </div>
                          <p className={`text-xs ${isDarkMode ? 'text-slate-400' : 'text-slate-600'}`}>
                            <span className="font-medium">{rule.field}</span> {rule.operator}{' '}
                            {rule.values && `[${rule.values.join(', ')}]`}
                          </p>
                          {rule.reason && (
                            <p className={`text-xs italic mt-1 ${isDarkMode ? 'text-slate-500' : 'text-slate-500'}`}>"{rule.reason}"</p>
                          )}
                        </div>
                      ))}
                    </div>
                  </div>
                )}

                {/* Raw JSON fallback for any other sections */}
                {Object.keys(policyData.content).filter(k => k !== 'budget' && k !== 'content_rules').length > 0 && (
                  <div className={`rounded-lg border p-4 ${isDarkMode ? 'bg-slate-900 border-slate-700' : 'bg-white border-slate-200'}`}>
                    <h3 className={`text-sm font-medium mb-3 ${isDarkMode ? 'text-slate-200' : 'text-slate-800'}`}>Other Settings</h3>
                    <div className={`rounded-md p-3 overflow-x-auto ${isDarkMode ? 'bg-slate-950 border border-slate-800' : 'bg-slate-900'}`}>
                      <pre className="text-xs text-slate-300 font-mono">
                        {JSON.stringify(
                          Object.fromEntries(Object.entries(policyData.content).filter(([k]) => k !== 'budget' && k !== 'content_rules')),
                          null, 2
                        )}
                      </pre>
                    </div>
                  </div>
                )}
              </div>
            )}
          </div>
        )}

        {/* ── Trace Tab ─────────────────────────────────────────────────── */}
        {activeTab === 'trace' && (
          <>
            {/* Cytoscape Canvas */}
            <div className={`flex-1 relative ${isDarkMode ? 'bg-slate-950' : 'bg-slate-50'}`}>
              {error && (
                <div className={`absolute top-4 left-1/2 transform -translate-x-1/2 px-4 py-3 rounded-md shadow-sm flex items-center space-x-2 z-20 border ${isDarkMode ? 'bg-red-950 border-red-900 text-red-200' : 'bg-red-50 border-red-200 text-red-700'}`}>
                  <AlertCircle className="h-5 w-5 flex-shrink-0" />
                  <p className="text-sm">{error}</p>
                </div>
              )}

              {!traceData && !loading && !error && (
                <div className="absolute inset-0 flex flex-col items-center justify-center text-slate-400 pointer-events-none">
                  <Info className="h-12 w-12 mb-4 opacity-50" />
                  <p className="text-lg font-medium">Enter a Run ID to view its trace</p>
                </div>
              )}

              {elements.length > 0 && (
                <CytoscapeComponent
                  elements={elements}
                  layout={layout}
                  stylesheet={stylesheet}
                  style={{ width: '100%', height: '100%' }}
                  className="w-full h-full"
                  cy={(cy) => { (window as any).cy = cy;
                    if (cyInstance !== cy) {
                      setCyInstance(cy); (window as any).cyInstance = cy;
                      cy.on('tap', 'node', handleNodeClick);
                      cy.on('tap', handleBackgroundClick);
                    }
                  }}
                />
              )}
            </div>

            {/* Sidebar / Details Panel */}
            {selectedEvent && (
              <div className={`w-96 border-l flex flex-col shadow-xl z-20 shrink-0 overflow-y-auto ${isDarkMode ? 'bg-slate-900 border-slate-800' : 'bg-white border-slate-200'}`}>
                <div className={`p-4 border-b sticky top-0 ${isDarkMode ? 'bg-slate-800 border-slate-700' : 'bg-slate-50 border-slate-100'}`}>
                  <h2 className="text-lg font-semibold flex items-center justify-between">
                    <span>Node: {selectedEvent.node}</span>
                    <span className={`text-xs px-2 py-1 rounded-full border ${
                        selectedEvent.status === 'ok' ? (isDarkMode ? 'bg-green-900 text-green-200 border-green-800' : 'bg-green-100 text-green-800 border-green-200') :
                        selectedEvent.status === 'blocked' || selectedEvent.status === 'error' ? (isDarkMode ? 'bg-red-900 text-red-200 border-red-800' : 'bg-red-100 text-red-800 border-red-200') :
                        selectedEvent.status === 'hitl_pending' ? (isDarkMode ? 'bg-yellow-900 text-yellow-200 border-yellow-800' : 'bg-yellow-100 text-yellow-800 border-yellow-200') :
                        (isDarkMode ? 'bg-slate-800 text-slate-200 border-slate-700' : 'bg-slate-100 text-slate-800 border-slate-200')
                    }`}>
                      {selectedEvent.status.toUpperCase()}
                    </span>
                  </h2>
                  <p className={`text-xs font-mono mt-1 break-all ${isDarkMode ? 'text-slate-400' : 'text-slate-500'}`}>{selectedEvent.event_id}</p>
                </div>

                <div className="p-4 space-y-6">

                  <div className="grid grid-cols-2 gap-4">
                    <div className={`p-3 rounded-lg border ${isDarkMode ? 'bg-slate-800 border-slate-700' : 'bg-slate-50 border-slate-100'}`}>
                      <div className={`flex items-center mb-1 ${isDarkMode ? 'text-slate-400' : 'text-slate-500'}`}>
                        <Clock className="h-4 w-4 mr-1" />
                        <span className="text-xs font-medium uppercase tracking-wider">Duration</span>
                      </div>
                      <div className="text-sm font-semibold">
                        {selectedEvent.duration_ms != null ? `${selectedEvent.duration_ms} ms` : 'N/A'}
                      </div>
                    </div>

                    <div className={`p-3 rounded-lg border ${isDarkMode ? 'bg-slate-800 border-slate-700' : 'bg-slate-50 border-slate-100'}`}>
                      <div className={`flex items-center mb-1 ${isDarkMode ? 'text-slate-400' : 'text-slate-500'}`}>
                        <DollarSign className="h-4 w-4 mr-1" />
                        <span className="text-xs font-medium uppercase tracking-wider">Cost</span>
                      </div>
                      <div className="text-sm font-semibold">
                        ${selectedEvent.cost_delta.toFixed(4)}
                      </div>
                    </div>
                  </div>

                  {/* Token Counts (schema 1.1) */}
                  {selectedEvent.llm_token_counts && (
                    <div className="space-y-3">
                      <h3 className={`text-sm font-medium border-b pb-1 flex items-center gap-1.5 ${isDarkMode ? 'text-slate-200 border-slate-700' : 'text-slate-900 border-slate-200'}`}>
                        <Cpu className="h-4 w-4 text-blue-500" />
                        Token Usage
                      </h3>
                      <div className="grid grid-cols-3 gap-2">
                        {Object.entries(selectedEvent.llm_token_counts).map(([k, v]) => (
                          <div key={k} className={`p-2 rounded border ${isDarkMode ? 'bg-slate-800 border-slate-700' : 'bg-slate-50 border-slate-100'}`}>
                            <span className={`text-xs block mb-0.5 ${isDarkMode ? 'text-slate-400' : 'text-slate-500'}`}>{k.replace('_tokens', '')}</span>
                            <span className="text-sm font-semibold font-mono">{v.toLocaleString()}</span>
                          </div>
                        ))}
                      </div>
                    </div>
                  )}

                  {/* Latency Breakdown (schema 1.1) */}
                  {selectedEvent.latency_breakdown && (
                    <div className="space-y-3">
                      <h3 className={`text-sm font-medium border-b pb-1 flex items-center gap-1.5 ${isDarkMode ? 'text-slate-200 border-slate-700' : 'text-slate-900 border-slate-200'}`}>
                        <Clock className="h-4 w-4 text-purple-500" />
                        Latency Breakdown
                      </h3>
                      <div className="grid grid-cols-3 gap-2">
                        {Object.entries(selectedEvent.latency_breakdown).map(([k, v]) => (
                          <div key={k} className={`p-2 rounded border ${isDarkMode ? 'bg-slate-800 border-slate-700' : 'bg-slate-50 border-slate-100'}`}>
                            <span className={`text-xs block mb-0.5 ${isDarkMode ? 'text-slate-400' : 'text-slate-500'}`}>{k.replace('_ms', '')}</span>
                            <span className="text-sm font-semibold font-mono">{v} ms</span>
                          </div>
                        ))}
                      </div>
                    </div>
                  )}

                  {/* Core Details */}
                  <div className="space-y-3">
                    <h3 className={`text-sm font-medium border-b pb-1 ${isDarkMode ? 'text-slate-200 border-slate-700' : 'text-slate-900 border-slate-200'}`}>Execution Details</h3>

                    {selectedEvent.agent_id && (
                      <div>
                        <span className={`text-xs block mb-1 ${isDarkMode ? 'text-slate-400' : 'text-slate-500'}`}>Agent ID</span>
                        <span className={`text-sm px-2 py-1 rounded font-mono ${isDarkMode ? 'bg-slate-800' : 'bg-slate-100'}`}>{selectedEvent.agent_id}</span>
                      </div>
                    )}

                    <div>
                      <span className={`text-xs block mb-1 ${isDarkMode ? 'text-slate-400' : 'text-slate-500'}`}>Depth</span>
                      <span className="text-sm">{selectedEvent.depth}</span>
                    </div>

                    {selectedEvent.action && (
                      <div>
                        <span className={`text-xs block mb-1 ${isDarkMode ? 'text-slate-400' : 'text-slate-500'}`}>Action</span>
                        <span className={`text-sm font-mono p-1 rounded break-all ${isDarkMode ? 'bg-slate-800' : 'bg-slate-50'}`}>{selectedEvent.action}</span>
                      </div>
                    )}

                    {selectedEvent.intent && (
                      <div>
                        <span className={`text-xs block mb-1 ${isDarkMode ? 'text-slate-400' : 'text-slate-500'}`}>Intent</span>
                        <p className={`text-sm p-2 rounded-md border whitespace-pre-wrap ${isDarkMode ? 'bg-slate-800 border-slate-700' : 'bg-slate-50 border-slate-100'}`}>
                          {selectedEvent.intent}
                        </p>
                      </div>
                    )}
                  </div>

                  {/* Governance Decision */}
                  {selectedEvent.decision && (
                    <div className="space-y-3">
                      <h3 className={`text-sm font-medium border-b pb-1 ${isDarkMode ? 'text-slate-200 border-slate-700' : 'text-slate-900 border-slate-200'}`}>Governance Decision</h3>
                      <div className={`p-3 rounded-md border ${
                          selectedEvent.decision.allowed
                            ? (isDarkMode ? 'bg-green-950 border-green-900' : 'bg-green-50 border-green-200')
                            : (isDarkMode ? 'bg-red-950 border-red-900' : 'bg-red-50 border-red-200')
                      }`}>
                        <div className="flex justify-between items-center mb-2">
                           <span className="text-xs font-semibold">Allowed: {selectedEvent.decision.allowed ? 'Yes' : 'No'}</span>
                           {selectedEvent.decision.hitl_required && (
                               <span className={`text-xs px-2 py-0.5 rounded border ${isDarkMode ? 'bg-yellow-950 text-yellow-200 border-yellow-800' : 'bg-yellow-100 text-yellow-800 border-yellow-300'}`}>HITL Required</span>
                           )}
                        </div>
                        {selectedEvent.decision.reason && (
                          <p className={`text-sm italic border-t pt-2 mt-2 ${isDarkMode ? 'text-slate-300 border-slate-700' : 'text-slate-700 border-white/50'}`}>
                            "{selectedEvent.decision.reason}"
                          </p>
                        )}
                      </div>
                    </div>
                  )}

                  {/* Metadata */}
                  {selectedEvent.metadata && Object.keys(selectedEvent.metadata).length > 0 && (
                    <div className="space-y-3">
                      <h3 className={`text-sm font-medium border-b pb-1 ${isDarkMode ? 'text-slate-200 border-slate-700' : 'text-slate-900 border-slate-200'}`}>Metadata</h3>
                      <div className={`rounded-md p-3 overflow-x-auto ${isDarkMode ? 'bg-slate-950 border border-slate-800' : 'bg-slate-900'}`}>
                        <pre className="text-xs text-slate-300 font-mono">
                          {JSON.stringify(selectedEvent.metadata, null, 2)}
                        </pre>
                      </div>
                    </div>
                  )}
                </div>
              </div>
            )}
          </>
        )}
      </div>
    </div>
  );
}

export default App;
