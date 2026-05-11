import { NavigationContainer } from "@react-navigation/native";
import {
  NativeStackScreenProps,
  createNativeStackNavigator,
} from "@react-navigation/native-stack";
import React from "react";
import { ActivityIndicator, View } from "react-native";

import { useAuth } from "../context/AuthContext";
import LoginScreen from "../screens/auth/LoginScreen";
import RegisterScreen from "../screens/auth/RegisterScreen";
import CompanyCreateScreen from "../screens/companies/CompanyCreateScreen";
import CompanyListScreen from "../screens/companies/CompanyListScreen";
import DashboardScreen from "../screens/dashboard/DashboardScreen";

type AuthStackParamList = {
  Login: undefined;
  Register: undefined;
};

type AppStackParamList = {
  Dashboard: undefined;
  Companies: undefined;
  CreateCompany: undefined;
};

const AuthStack = createNativeStackNavigator<AuthStackParamList>();
const AppStack = createNativeStackNavigator<AppStackParamList>();


function AuthFlow(): React.ReactElement {
  return (
    <AuthStack.Navigator screenOptions={{ headerShown: false }}>
      <AuthStack.Screen name="Login">
        {(props: NativeStackScreenProps<AuthStackParamList, "Login">) => (
          <LoginScreen
            onNavigateToRegister={() => props.navigation.navigate("Register")}
          />
        )}
      </AuthStack.Screen>
      <AuthStack.Screen name="Register">
        {(props: NativeStackScreenProps<AuthStackParamList, "Register">) => (
          <RegisterScreen
            onSuccess={() => props.navigation.replace("Login")}
            onBackToLogin={() => props.navigation.goBack()}
          />
        )}
      </AuthStack.Screen>
    </AuthStack.Navigator>
  );
}


function AppFlow(): React.ReactElement {
  return (
    <AppStack.Navigator>
      <AppStack.Screen
        name="Dashboard"
        options={{ title: "TaxMind Books" }}
      >
        {(props: NativeStackScreenProps<AppStackParamList, "Dashboard">) => (
          <DashboardScreen
            onOpenCompanies={() => props.navigation.navigate("Companies")}
          />
        )}
      </AppStack.Screen>
      <AppStack.Screen name="Companies" options={{ title: "Companies" }}>
        {(props: NativeStackScreenProps<AppStackParamList, "Companies">) => (
          <CompanyListScreen
            onCreate={() => props.navigation.navigate("CreateCompany")}
            onPick={() => props.navigation.navigate("Dashboard")}
          />
        )}
      </AppStack.Screen>
      <AppStack.Screen name="CreateCompany" options={{ title: "New company" }}>
        {(
          props: NativeStackScreenProps<AppStackParamList, "CreateCompany">,
        ) => (
          <CompanyCreateScreen
            onCreated={() =>
              props.navigation.reset({
                index: 0,
                routes: [{ name: "Dashboard" }],
              })
            }
            onCancel={() => props.navigation.goBack()}
          />
        )}
      </AppStack.Screen>
    </AppStack.Navigator>
  );
}


export default function RootNavigator(): React.ReactElement {
  const { loading, user } = useAuth();
  if (loading) {
    return (
      <View style={{ flex: 1, justifyContent: "center", alignItems: "center" }}>
        <ActivityIndicator size="large" />
      </View>
    );
  }
  return (
    <NavigationContainer>
      {user === null ? <AuthFlow /> : <AppFlow />}
    </NavigationContainer>
  );
}
