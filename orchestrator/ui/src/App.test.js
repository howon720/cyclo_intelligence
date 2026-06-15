import { render, screen } from '@testing-library/react';
import { Provider } from 'react-redux';
import App from './App';
import { ThemeProvider } from './contexts/ThemeContext';
import store from './store/store';

jest.mock('./components/ThemeToggle', () => {
  const React = require('react');
  return function MockThemeToggle() {
    return React.createElement('button', { type: 'button' }, 'Theme');
  };
});

jest.mock('./pages/HomePage', () => {
  const React = require('react');
  return function MockHomePage() {
    return React.createElement('div', null, 'Home Page');
  };
});

jest.mock('./pages/RecordPage', () => {
  const React = require('react');
  return function MockRecordPage() {
    return React.createElement('div', null, 'Record Page');
  };
});

jest.mock('./pages/InferencePage', () => {
  const React = require('react');
  return function MockInferencePage() {
    return React.createElement('div', null, 'Inference Page');
  };
});

jest.mock('./pages/TrainingPage', () => {
  const React = require('react');
  return function MockTrainingPage() {
    return React.createElement('div', null, 'Training Page');
  };
});

jest.mock('./pages/EditDatasetPage', () => {
  const React = require('react');
  return function MockEditDatasetPage() {
    return React.createElement('div', null, 'Edit Dataset Page');
  };
});

jest.mock('./pages/ReplayPage', () => {
  const React = require('react');
  return function MockReplayPage() {
    return React.createElement('div', null, 'Replay Page');
  };
});

jest.mock('./pages/BTManagerPage', () => {
  const React = require('react');
  return function MockBTManagerPage() {
    return React.createElement('div', null, 'BT Manager Page');
  };
});

jest.mock('./hooks/useRosTopicSubscription', () => ({
  useRosTopicSubscription: () => ({
    initializeSubscriptions: jest.fn(),
  }),
}));

jest.mock('./utils/rosConnectionManager', () => ({
  __esModule: true,
  default: {
    setOnConnected: jest.fn(),
    disconnect: jest.fn(),
  },
}));

test('renders the Cyclo Intelligence shell navigation', () => {
  render(
    <Provider store={store}>
      <ThemeProvider>
        <App />
      </ThemeProvider>
    </Provider>
  );

  expect(screen.getByRole('button', { name: 'Cyclo Intelligence' }))
    .toBeInTheDocument();
  expect(screen.getByRole('button', { name: /Inference/i }))
    .toBeInTheDocument();
  expect(screen.getByText('Home Page')).toBeInTheDocument();
});
