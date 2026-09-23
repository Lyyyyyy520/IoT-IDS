import React from 'react';
import { ActivityIndicator, Text, TouchableOpacity, View } from 'react-native';
import { NavigationContainer, DefaultTheme } from '@react-navigation/native';
import { createBottomTabNavigator } from '@react-navigation/bottom-tabs';
import { Ionicons } from '@expo/vector-icons';
import { useMobile } from '../mobile/MobileContext';
import PairingScreen from '../mobile/PairingScreen';
import HomeScreen from '../mobile/HomeScreen';
import DevicesScreen from '../mobile/DevicesScreen';
import SettingsScreen from '../mobile/SettingsScreen';
import { palette, ui } from '../mobile/ui';

const Tab = createBottomTabNavigator();
const theme = { ...DefaultTheme, colors: { ...DefaultTheme.colors,
  background: palette.background, card: palette.card, primary: palette.green,
  text: palette.text, border: palette.border } };

export default function RootNavigator() {
  const auth = useMobile();
  if (auth.phase === 'restoring') return <View style={[ui.page, { justifyContent: 'center', alignItems: 'center' }]}>
    <ActivityIndicator color={palette.green} /><Text style={ui.muted}>正在安全恢复会话…</Text>
  </View>;
  if (auth.phase === 'offline-with-session') return <View style={[ui.page, ui.content, { justifyContent: 'center' }]}>
    <Text style={ui.title}>暂时无法连接服务器</Text>
    <Text style={ui.body}>本机凭据仍保留。请检查网络后重试；不会显示缓存的设备安全结论。</Text>
    {auth.error && <Text accessibilityRole="alert" style={ui.warning}>{auth.error}</Text>}
    <TouchableOpacity accessibilityRole="button" style={ui.button} onPress={() => void auth.sync()}><Text style={ui.buttonText}>重试连接</Text></TouchableOpacity>
    <TouchableOpacity accessibilityRole="button" style={ui.secondaryButton} onPress={() => void auth.logout()}><Text style={ui.secondaryText}>清除此设备配对</Text></TouchableOpacity>
  </View>;
  if (auth.phase !== 'authenticated') return <PairingScreen />;
  return <NavigationContainer theme={theme}>
    <Tab.Navigator screenOptions={({ route }) => ({
      headerStyle: { backgroundColor: palette.card }, headerTintColor: palette.text,
      tabBarStyle: { backgroundColor: palette.card }, tabBarActiveTintColor: palette.green,
      tabBarIcon: ({ color, size }) => <Ionicons name={route.name === '首页' ? 'home-outline' :
        route.name === '本人设备' ? 'cube-outline' : 'settings-outline'} size={size} color={color} />,
    })}>
      <Tab.Screen name="首页" component={HomeScreen} />
      <Tab.Screen name="本人设备" component={DevicesScreen} />
      <Tab.Screen name="设置" component={SettingsScreen} />
    </Tab.Navigator>
  </NavigationContainer>;
}
