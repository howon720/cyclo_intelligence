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
});
