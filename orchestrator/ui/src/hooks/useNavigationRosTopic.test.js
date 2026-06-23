import {
  navigationGridWebSocketUrl,
  wrapNavigationRosMessage,
} from './useNavigationRosTopic';

test('wraps OccupancyGrid without losing its data and metadata fields', () => {
  const map = {
    header: { frame_id: 'map' },
    info: { width: 2, height: 1, resolution: 0.05 },
    data: [0, 100],
  };

  expect(wrapNavigationRosMessage(map)).toEqual({
    available: true,
    data: map,
  });
});

test('builds a same-origin supervisor WebSocket URL for a grid topic', () => {
  expect(navigationGridWebSocketUrl('/global_costmap/costmap', {
    protocol: 'https:',
    host: 'robot.local:8443',
  })).toBe(
    'wss://robot.local:8443/api/navigation/topics/ws?topic=%2Fglobal_costmap%2Fcostmap'
  );
});
