import React from 'react';
import { ActivityIndicator, View, StyleSheet, TouchableOpacity } from 'react-native';
import { NavigationContainer, DarkTheme } from '@react-navigation/native';
import { createNativeStackNavigator } from '@react-navigation/native-stack';
import { createBottomTabNavigator } from '@react-navigation/bottom-tabs';
import { Ionicons } from '@expo/vector-icons';
import { useAuth } from '../context/AuthContext';
import { colors } from '../theme';

import LoginScreen from '../screens/LoginScreen';
import DashboardScreen from '../screens/DashboardScreen';
import AssetsScreen from '../screens/AssetsScreen';
import MonitorScreen from '../screens/MonitorScreen';
import AnalysisScreen from '../screens/AnalysisScreen';
import AlertsScreen from '../screens/AlertsScreen';
import HistoryScreen from '../screens/HistoryScreen';
import AlertDetailScreen from '../screens/AlertDetailScreen';
import SettingsScreen from '../screens/SettingsScreen';

const Stack = createNativeStackNavigator();
const Tab = createBottomTabNavigator();

const navTheme = {
  ...DarkTheme,
  colors: {
    ...DarkTheme.colors,
    primary: colors.accentCyan,
    background: colors.bgBase,
    card: colors.bgElevated,
    text: colors.textPrimary,
    border: colors.border,
  },
};

type IoniconName = keyof typeof Ionicons.glyphMap;

function tabIcon(route: string, focused: boolean): IoniconName {
  switch (route) {
    case 'Dashboard':
      return focused ? 'shield-checkmark' : 'shield-checkmark-outline';
    case 'Assets':
      return focused ? 'hardware-chip' : 'hardware-chip-outline';
    case 'Monitor':
      return focused ? 'pulse' : 'pulse-outline';
    case 'Analysis':
      return focused ? 'analytics' : 'analytics-outline';
    case 'Alerts':
      return focused ? 'warning' : 'warning-outline';
    case 'History':
      return focused ? 'time' : 'time-outline';
    default:
      return 'ellipse';
  }
}

function MainTabs() {
  return (
    <Tab.Navigator
      screenOptions={({ route }) => ({
        headerStyle: { backgroundColor: colors.bgElevated },
        headerTintColor: colors.textPrimary,
        headerTitleStyle: { fontWeight: '700' },
        headerShadowVisible: false,
        tabBarStyle: {
          backgroundColor: colors.bgElevated,
          borderTopColor: colors.border,
        },
        tabBarActiveTintColor: colors.accentCyan,
        tabBarInactiveTintColor: colors.textMuted,
        tabBarIcon: ({ focused, color, size }) => (
          <Ionicons name={tabIcon(route.name, focused)} size={size} color={color} />
        ),
      })}
    >
      <Tab.Screen
        name="Dashboard"
        component={DashboardScreen}
        options={({ navigation }) => ({
          title: '态势大屏',
          headerRight: () => (
            <TouchableOpacity
              onPress={() => navigation.navigate('Settings' as never)}
              style={{ paddingHorizontal: 14 }}
              hitSlop={{ top: 8, bottom: 8, left: 8, right: 8 }}
            >
              <Ionicons name="settings-outline" size={22} color={colors.textPrimary} />
            </TouchableOpacity>
          ),
        })}
      />
      <Tab.Screen name="Assets" component={AssetsScreen} options={{ title: '设备列表' }} />
      <Tab.Screen name="Monitor" component={MonitorScreen} options={{ title: '数据监控' }} />
      <Tab.Screen name="Analysis" component={AnalysisScreen} options={{ title: '分析视图' }} />
      <Tab.Screen name="Alerts" component={AlertsScreen} options={{ title: '入侵告警' }} />
      <Tab.Screen name="History" component={HistoryScreen} options={{ title: '历史记录' }} />
    </Tab.Navigator>
  );
}

function SplashScreen() {
  return (
    <View style={styles.splash}>
      <ActivityIndicator size="large" color={colors.accentCyan} />
    </View>
  );
}

export default function RootNavigator() {
  const { authenticated, loading } = useAuth();

  if (loading) return <SplashScreen />;

  return (
    <NavigationContainer theme={navTheme}>
      <Stack.Navigator
        screenOptions={{
          headerStyle: { backgroundColor: colors.bgElevated },
          headerTintColor: colors.textPrimary,
          headerTitleStyle: { fontWeight: '700' },
          headerShadowVisible: false,
        }}
      >
        {authenticated ? (
          <>
            <Stack.Screen name="Tabs" component={MainTabs} options={{ headerShown: false }} />
            <Stack.Screen
              name="AlertDetail"
              component={AlertDetailScreen}
              options={{ title: '告警详情' }}
            />
            <Stack.Screen
              name="Settings"
              component={SettingsScreen}
              options={{ title: '系统设置' }}
            />
          </>
        ) : (
          <Stack.Screen
            name="Login"
            component={LoginScreen}
            options={{ headerShown: false }}
          />
        )}
      </Stack.Navigator>
    </NavigationContainer>
  );
}

const styles = StyleSheet.create({
  splash: {
    flex: 1,
    backgroundColor: colors.bgBase,
    alignItems: 'center',
    justifyContent: 'center',
  },
});
