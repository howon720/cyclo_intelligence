import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import PolicyBackendControl from './PolicyBackendControl';

jest.mock('react-hot-toast', () => ({
  success: jest.fn(),
  error: jest.fn(),
}));

function mockResponse(data, options = {}) {
  return {
    ok: options.ok ?? true,
    status: options.status ?? 200,
    body: options.body,
    text: async () => (
      typeof data === 'string' ? data : JSON.stringify(data)
    ),
  };
}

describe('PolicyBackendControl', () => {
  beforeEach(() => {
    global.fetch = jest.fn();
  });

  afterEach(() => {
    jest.restoreAllMocks();
  });

  it('shows only pull controls until the backend image exists', async () => {
    global.fetch
      .mockResolvedValueOnce(mockResponse({
        name: 'groot',
        container_state: 'not_created',
        image_pulled: false,
        services: [],
      }))
      .mockResolvedValueOnce(mockResponse('', { body: null }))
      .mockResolvedValueOnce(mockResponse({
        name: 'groot',
        container_state: 'not_created',
        image_pulled: true,
        services: [],
      }));

    render(<PolicyBackendControl serviceType="groot" />);

    const pullButton = await screen.findByRole('button', {
      name: 'GR00T Docker pull image',
    });
    expect(screen.queryByRole('button', { name: 'GR00T Docker on' }))
      .not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'GR00T Docker restart' }))
      .not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'GR00T Docker off' }))
      .not.toBeInTheDocument();

    fireEvent.click(pullButton);

    await waitFor(() => {
      expect(global.fetch).toHaveBeenCalledWith(
        '/api/backends/groot/pull',
        { method: 'POST' }
      );
    });
    await waitFor(() => {
      expect(screen.getByText('Image ready. Press ON.')).toBeInTheDocument();
    });
  });

  it('shows an explicit update action when the existing container image is stale', async () => {
    global.fetch
      .mockResolvedValueOnce(mockResponse({
        name: 'groot',
        image: 'robotis/groot-zenoh:1.3.0-arm64',
        image_pulled: true,
        image_status: 'stale',
        container_state: 'exited',
        raw_state: 'stale_image',
        services: [],
      }))
      .mockResolvedValueOnce(mockResponse({ ok: true, message: 'recreated' }))
      .mockResolvedValueOnce(mockResponse({
        name: 'groot',
        image: 'robotis/groot-zenoh:1.3.0-arm64',
        image_pulled: true,
        image_status: 'current',
        container_state: 'exited',
        services: [],
      }));

    render(<PolicyBackendControl serviceType="groot" />);

    const updateButton = await screen.findByRole('button', {
      name: 'GR00T Docker update container',
    });

    expect(screen.getByText('Update required')).toBeInTheDocument();
    expect(screen.getByText('Container outdated')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'GR00T Docker on' }))
      .not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'GR00T Docker restart' }))
      .not.toBeInTheDocument();

    fireEvent.click(updateButton);

    await waitFor(() => {
      expect(global.fetch).toHaveBeenCalledWith(
        '/api/backends/groot/recreate',
        { method: 'POST' }
      );
    });
  });
});
