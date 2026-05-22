import { DEFAULT_PATHS } from '../../constants/paths';

export const DOWNLOAD_MODEL_BACKENDS = [
  {
    value: 'lerobot',
    label: 'LeRobot',
    path: DEFAULT_PATHS.LEROBOT_CHECKPOINTS_PATH,
  },
  {
    value: 'groot',
    label: 'GR00T',
    path: DEFAULT_PATHS.GROOT_CHECKPOINTS_PATH,
  },
];

const MANAGED_DOWNLOAD_PATHS = new Set([
  DEFAULT_PATHS.HF_MODEL_DOWNLOAD_PATH,
  DEFAULT_PATHS.HF_DATASET_DOWNLOAD_PATH,
  ...DOWNLOAD_MODEL_BACKENDS.map((option) => option.path),
]);

export const getDefaultDownloadPath = (downloadType, modelBackend = 'lerobot') => {
  if (downloadType !== 'model') {
    return DEFAULT_PATHS.HF_DATASET_DOWNLOAD_PATH;
  }

  return (
    DOWNLOAD_MODEL_BACKENDS.find((option) => option.value === modelBackend)?.path ||
    DEFAULT_PATHS.LEROBOT_CHECKPOINTS_PATH
  );
};

export const isManagedDownloadPath = (path) =>
  MANAGED_DOWNLOAD_PATHS.has((path || '').replace(/\/$/, ''));
