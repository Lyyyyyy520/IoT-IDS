import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';

it('registers only ordinary-user routes and never mounts legacy admin authentication', () => {
  const root = resolve(__dirname, '../..');
  const navigation = readFileSync(resolve(root, 'src/navigation/index.tsx'), 'utf8');
  const app = readFileSync(resolve(root, 'App.tsx'), 'utf8');
  expect(navigation).toContain('name="首页"');
  expect(navigation).toContain('name="安全提醒"');
  expect(navigation).toContain('name="本人设备"');
  expect(navigation).toContain('name="设置"');
  for (const workflow of ['提醒详情', '设备详情', '提交求助', '我的求助', '求助详情']) expect(navigation).toContain(`name="${workflow}"`);
  for (const legacy of ['LoginScreen', 'MonitorScreen', 'AnalysisScreen', 'AlertDetailScreen',
    'AssetsScreen', 'HistoryScreen', 'DashboardScreen']) {
    expect(navigation).not.toContain(legacy);
  }
  expect(app).toContain('MobileProvider');
  expect(app).not.toContain('AuthProvider');
});
