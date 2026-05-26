// Copyright 2025 ROBOTIS CO., LTD.
//
// Licensed under the Apache License, Version 2.0 (the "License");
// you may not use this file except in compliance with the License.
// You may obtain a copy of the License at
//
//     http://www.apache.org/licenses/LICENSE-2.0

import React, { useCallback, useEffect, useMemo, useState } from 'react';
import clsx from 'clsx';
import toast from 'react-hot-toast';
import { MdPowerSettingsNew, MdRefresh, MdStop } from 'react-icons/md';
import Tooltip from './Tooltip';
import { getPolicyBackendReadiness } from '../hooks/usePolicyBackendStatus';

const API_BASE = '/api';

const stateLabels = {
  running: 'Running',
  exited: 'Stopped',
  not_created: 'Not created',
  unknown: 'Unknown',
};

const processLabels = {
  'main-runtime': 'Main',
  'engine-process': 'Engine',
};

const getBackendLabel = (serviceType) => {
  if (serviceType === 'groot') return 'GR00T Docker';
  if (serviceType === 'lerobot') return 'LeRobot Docker';
  return 'Policy Docker';
};

async function readJsonResponse(response) {
  const text = await response.text();
  if (!text) return {};
  try {
    return JSON.parse(text);
  } catch {
    return { detail: text };
  }
}

export default function PolicyBackendControl({ serviceType }) {
  const backend = serviceType === 'groot' ? 'groot' : 'lerobot';
  const label = useMemo(
    () => getBackendLabel(serviceType),
    [serviceType]
  );

  const [status, setStatus] = useState(null);
  const [isRefreshing, setIsRefreshing] = useState(false);
  const [pendingAction, setPendingAction] = useState(null);

  const refreshStatus = useCallback(async ({ quiet = false } = {}) => {
    if (!quiet) setIsRefreshing(true);
    try {
      const response = await fetch(`${API_BASE}/backends/${backend}/status`);
      const data = await readJsonResponse(response);
      if (!response.ok) {
        throw new Error(data.detail || `status failed (${response.status})`);
      }
      setStatus(data);
    } catch (error) {
      setStatus({
        container_state: 'unknown',
        image_pulled: false,
        raw_state: error.message,
      });
      if (!quiet) toast.error(`${label} status failed: ${error.message}`);
    } finally {
      if (!quiet) setIsRefreshing(false);
    }
  }, [backend, label]);

  useEffect(() => {
    refreshStatus({ quiet: true });
    const id = setInterval(() => refreshStatus({ quiet: true }), 5000);
    return () => clearInterval(id);
  }, [refreshStatus]);

  const callBackend = useCallback(async (action, successLabel) => {
    setPendingAction(action);
    try {
      const response = await fetch(`${API_BASE}/backends/${backend}/${action}`, {
        method: 'POST',
      });
      const data = await readJsonResponse(response);
      if (!response.ok || data.ok === false) {
        throw new Error(data.detail || data.message || `${action} failed`);
      }
      toast.success(`${label} ${successLabel}`);
      await refreshStatus({ quiet: true });
    } catch (error) {
      toast.error(`${label} ${action} failed: ${error.message}`);
      await refreshStatus({ quiet: true });
    } finally {
      setPendingAction(null);
    }
  }, [backend, label, refreshStatus]);

  const state = status?.container_state || 'unknown';
  const isBusy = Boolean(pendingAction) || isRefreshing;
  const isRunning = state === 'running';
  const imagePulled = Boolean(status?.image_pulled);
  const readiness = useMemo(() => getPolicyBackendReadiness(status), [status]);
  const isWarming = isRunning && !readiness.ready &&
    (readiness.state === 'checking' || readiness.state === 'warming');
  const statusLabel = isWarming
    ? 'Warming up'
    : stateLabels[state] || stateLabels.unknown;
  const serviceByName = useMemo(() => {
    const byName = {};
    for (const service of status?.services || []) {
      byName[service.name] = service;
    }
    return byName;
  }, [status]);

  const statusClass = clsx(
    'text-xs',
    'font-semibold',
    'px-2',
    'py-0.5',
    'rounded-full',
    {
      'bg-green-100 text-green-700': isRunning && readiness.ready,
      'bg-gray-100 text-gray-600': state === 'exited' || state === 'not_created',
      'bg-yellow-100 text-yellow-700': state === 'unknown' || isWarming,
    }
  );

  const serviceStatusClass = (serviceState) => clsx(
    'text-xs',
    'font-semibold',
    'px-2',
    'py-0.5',
    'rounded-full',
    {
      'bg-green-100 text-green-700': serviceState === 'up',
      'bg-red-100 text-red-700': serviceState === 'down',
      'bg-yellow-100 text-yellow-700': serviceState !== 'up' && serviceState !== 'down',
    }
  );

  const buttonClass = (variant) => clsx(
    'h-8',
    'px-2.5',
    'rounded-md',
    'text-sm',
    'font-semibold',
    'flex',
    'items-center',
    'justify-center',
    'gap-1',
    'transition-colors',
    'disabled:opacity-40',
    'disabled:cursor-not-allowed',
    {
      'bg-blue-500 text-white hover:bg-blue-600': variant === 'on',
      'bg-gray-500 text-white hover:bg-gray-600': variant === 'restart',
      'bg-red-500 text-white hover:bg-red-600': variant === 'off',
    }
  );

  return (
    <div className="mb-3 border-t border-b border-gray-200 py-2">
      <div className="flex items-center justify-between gap-2 mb-2">
        <div className="text-sm font-semibold text-gray-700">{label}</div>
        <div className="flex items-center gap-1.5">
          <span className={statusClass}>
            {statusLabel}
          </span>
          {!imagePulled && (
            <Tooltip
              position="bottom"
              content="Image is not available locally. Connect internet and pull/install before first start."
            >
              <span className="text-xs font-semibold text-yellow-700 bg-yellow-100 px-2 py-0.5 rounded-full">
                Image missing
              </span>
            </Tooltip>
          )}
        </div>
      </div>
      <div className="grid grid-cols-3 gap-2">
        <button
          type="button"
          className={buttonClass('on')}
          disabled={isBusy}
          onClick={() => callBackend('start', 'started')}
          aria-label={`${label} on`}
        >
          <MdPowerSettingsNew size={16} />
          ON
        </button>
        <button
          type="button"
          className={buttonClass('restart')}
          disabled={isBusy}
          onClick={() => callBackend('restart', 'restarted')}
          aria-label={`${label} restart`}
        >
          <MdRefresh size={16} />
          Restart
        </button>
        <button
          type="button"
          className={buttonClass('off')}
          disabled={isBusy}
          onClick={() => callBackend('stop', 'stopped')}
          aria-label={`${label} off`}
        >
          <MdStop size={16} />
          OFF
        </button>
      </div>
      {isRunning && (
        <>
          <div className="mt-2 grid grid-cols-2 gap-2">
            {['main-runtime', 'engine-process'].map((name) => {
              const service = serviceByName[name] || { state: 'unknown', raw: 'not reported' };
              const displayState = service.state === 'up'
                ? 'Up'
                : service.state === 'down'
                  ? 'Down'
                  : 'Unknown';
              return (
                <Tooltip
                  key={name}
                  position="bottom"
                  content={service.raw || displayState}
                >
                  <div className="flex items-center justify-between gap-2 rounded-md bg-gray-50 px-2 py-1">
                    <span className="text-xs font-medium text-gray-600">
                      {processLabels[name]}
                    </span>
                    <span className={serviceStatusClass(service.state)}>
                      {displayState}
                    </span>
                  </div>
                </Tooltip>
              );
            })}
          </div>
          {!readiness.ready && (
            <div className="mt-1 text-xs font-medium text-yellow-700">
              {readiness.message}
            </div>
          )}
        </>
      )}
    </div>
  );
}
