import { StyleSheet } from 'react-native';

export const palette = {
  background: '#F7F5F0', card: '#FFFFFF', text: '#292D2A', muted: '#616962',
  green: '#286C4A', amber: '#916515', coral: '#B94B3E', border: '#DDDCD5',
};
export const ui = StyleSheet.create({
  page: { flex: 1, backgroundColor: palette.background },
  content: { padding: 20, gap: 16 },
  title: { color: palette.text, fontSize: 27, fontWeight: '700' },
  subtitle: { color: palette.muted, fontSize: 15, lineHeight: 22 },
  card: { backgroundColor: palette.card, borderRadius: 20, borderWidth: 1,
    borderColor: palette.border, padding: 18, gap: 9 },
  cardTitle: { color: palette.text, fontSize: 18, fontWeight: '700' },
  body: { color: palette.text, fontSize: 15, lineHeight: 22 },
  muted: { color: palette.muted, fontSize: 14, lineHeight: 21 },
  warning: { color: palette.coral, fontSize: 14, lineHeight: 21 },
  label: { color: palette.text, fontSize: 15, fontWeight: '600' },
  input: { backgroundColor: '#fff', borderWidth: 1, borderColor: palette.border,
    borderRadius: 12, color: palette.text, fontSize: 16, padding: 13 },
  button: { backgroundColor: palette.green, padding: 15, borderRadius: 12, alignItems: 'center' },
  buttonText: { color: '#fff', fontWeight: '700', fontSize: 16 },
  secondaryButton: { padding: 13, borderWidth: 1, borderColor: palette.border,
    borderRadius: 12, alignItems: 'center' },
  secondaryText: { color: palette.text, fontWeight: '600', fontSize: 15 },
});
