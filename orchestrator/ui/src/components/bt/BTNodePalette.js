// Copyright 2026 ROBOTIS CO., LTD.
//
// Licensed under the Apache License, Version 2.0 (the "License");
// you may not use this file except in compliance with the License.
// You may obtain a copy of the License at
//
//     http://www.apache.org/licenses/LICENSE-2.0
//
// Unless required by applicable law or agreed to in writing, software
// distributed under the License is distributed on an "AS IS" BASIS,
// WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
// See the License for the specific language governing permissions and
// limitations under the License.
//
// Author: Seongwoo Kim

import { useMemo } from 'react';
import { MdRefresh } from 'react-icons/md';
import { useBTNodeCatalog } from '../../hooks/useBTNodeCatalog';

export const PALETTE_DRAG_MIME = 'application/bt-node-tag';

export default function BTNodePalette({ canUpdateCatalog = true }) {
  const { catalog, source, refreshCatalog } = useBTNodeCatalog();
  const isUpdating = source === 'loading';
  const isUpdateDisabled = !canUpdateCatalog || isUpdating;
  const updateTitle = canUpdateCatalog
    ? 'Update node list from /bt/nodes/catalog'
    : 'Start BT Node to update the node list';

  const grouped = useMemo(() => ({
    control: catalog.filter((n) => n.category === 'control'),
    action: catalog.filter((n) => n.category === 'action'),
  }), [catalog]);

  const handleDragStart = (event, tag) => {
    event.dataTransfer.setData(PALETTE_DRAG_MIME, tag);
    // Plain-text fallback for browsers that ignore custom MIME types.
    event.dataTransfer.setData('text/plain', tag);
    event.dataTransfer.effectAllowed = 'move';
  };

  return (
    <div className="w-[180px] shrink-0 bg-white border-r border-gray-200 flex flex-col">
      <div className="px-3 py-3 border-b border-gray-200">
        <button
          type="button"
          onClick={() => refreshCatalog({ force: true })}
          disabled={isUpdateDisabled}
          className={`w-full flex items-center justify-center gap-2 px-3 py-2 rounded text-sm font-medium transition-colors ${
            isUpdateDisabled
              ? 'bg-gray-100 text-gray-400 cursor-not-allowed'
              : 'bg-blue-50 text-blue-700 hover:bg-blue-100'
          }`}
          title={updateTitle}
          aria-label="Update node list"
        >
          <MdRefresh size={17} />
          {isUpdating ? (
            <span>Updating...</span>
          ) : (
            <span className="leading-tight text-center">
              <span className="block">Update</span>
              <span className="block">Node List</span>
            </span>
          )}
        </button>
      </div>

      <div className="flex-1 overflow-y-auto py-2">
        <Section
          title="Controls"
          accentClass="border-blue-300 text-blue-700"
          items={grouped.control}
          onDragStart={handleDragStart}
        />
        <Section
          title="Actions"
          accentClass="border-green-300 text-green-700"
          items={grouped.action}
          onDragStart={handleDragStart}
        />
      </div>
    </div>
  );
}

function Section({ title, items, accentClass, onDragStart }) {
  return (
    <div className="mb-2">
      <div className="px-3 py-1 text-[10px] uppercase tracking-wider text-gray-400 font-semibold">
        {title}
      </div>
      <div className="px-2 space-y-1">
        {items.map((item) => (
          <div
            key={item.tag}
            draggable
            onDragStart={(e) => onDragStart(e, item.tag)}
            className={`px-2 py-1.5 border ${accentClass} bg-white rounded text-xs cursor-grab active:cursor-grabbing hover:shadow-sm hover:-translate-y-0.5 transition-all select-none`}
            title={item.tag}
          >
            {item.tag}
          </div>
        ))}
      </div>
    </div>
  );
}
