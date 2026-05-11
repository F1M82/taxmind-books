import { StatusBar } from "expo-status-bar";
import React from "react";
import { SafeAreaProvider } from "react-native-safe-area-context";

import { AuthProvider } from "./src/context/AuthContext";
import { CompanyProvider } from "./src/context/CompanyContext";
import RootNavigator from "./src/navigation/RootNavigator";

export default function App(): React.ReactElement {
  return (
    <SafeAreaProvider>
      <AuthProvider>
        <CompanyProvider>
          <RootNavigator />
          <StatusBar style="auto" />
        </CompanyProvider>
      </AuthProvider>
    </SafeAreaProvider>
  );
}
