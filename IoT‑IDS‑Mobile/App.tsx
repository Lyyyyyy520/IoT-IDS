import React from 'react';
import { StatusBar } from 'expo-status-bar';
import { SafeAreaProvider } from 'react-native-safe-area-context';
import { MobileProvider } from './src/mobile/MobileContext';
import RootNavigator from './src/navigation';

export default function App() {
  return (
    <SafeAreaProvider>
      <MobileProvider>
        <StatusBar style="dark" />
        <RootNavigator />
      </MobileProvider>
    </SafeAreaProvider>
  );
}
