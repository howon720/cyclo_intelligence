import { DEFAULT_PATHS } from '../../constants/paths';
import {
  DOWNLOAD_MODEL_BACKENDS,
  getDefaultDownloadPath,
  isManagedDownloadPath,
} from './hfDownloadPaths';

describe('hfDownloadPaths', () => {
  test('routes model downloads to backend checkpoint dropboxes', () => {
    expect(getDefaultDownloadPath('model', 'lerobot')).toBe(
      DEFAULT_PATHS.LEROBOT_CHECKPOINTS_PATH
    );
    expect(getDefaultDownloadPath('model', 'groot')).toBe(
      DEFAULT_PATHS.GROOT_CHECKPOINTS_PATH
    );
  });

  test('keeps dataset downloads under rosbag2', () => {
    expect(getDefaultDownloadPath('dataset', 'groot')).toBe(
      DEFAULT_PATHS.HF_DATASET_DOWNLOAD_PATH
    );
  });

  test('tracks all automatic download destinations but leaves custom paths alone', () => {
    for (const option of DOWNLOAD_MODEL_BACKENDS) {
      expect(isManagedDownloadPath(option.path)).toBe(true);
    }
    expect(isManagedDownloadPath(DEFAULT_PATHS.HF_DATASET_DOWNLOAD_PATH)).toBe(true);
    expect(isManagedDownloadPath(DEFAULT_PATHS.HF_MODEL_DOWNLOAD_PATH)).toBe(true);
    expect(isManagedDownloadPath('/workspace/custom_models')).toBe(false);
  });
});
