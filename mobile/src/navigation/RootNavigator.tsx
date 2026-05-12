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
import LedgerListScreen from "../screens/ledgers/LedgerListScreen";
import BalanceSheetScreen from "../screens/reports/BalanceSheetScreen";
import OutstandingScreen from "../screens/reports/OutstandingScreen";
import ProfitLossScreen from "../screens/reports/ProfitLossScreen";
import TrialBalanceScreen from "../screens/reports/TrialBalanceScreen";
import VoucherEntryScreen from "../screens/vouchers/VoucherEntryScreen";
import VoucherListScreen from "../screens/vouchers/VoucherListScreen";

type AuthStackParamList = {
  Login: undefined;
  Register: undefined;
};

type AppStackParamList = {
  Dashboard: undefined;
  Companies: undefined;
  CreateCompany: undefined;
  Ledgers: undefined;
  Vouchers: undefined;
  NewVoucher: undefined;
  TrialBalance: undefined;
  ProfitLoss: undefined;
  BalanceSheet: undefined;
  Outstanding: undefined;
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
            onOpenLedgers={() => props.navigation.navigate("Ledgers")}
            onOpenVouchers={() => props.navigation.navigate("Vouchers")}
            onOpenTrialBalance={() => props.navigation.navigate("TrialBalance")}
            onOpenProfitLoss={() => props.navigation.navigate("ProfitLoss")}
            onOpenBalanceSheet={() =>
              props.navigation.navigate("BalanceSheet")
            }
            onOpenOutstanding={() => props.navigation.navigate("Outstanding")}
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
      <AppStack.Screen
        name="Ledgers"
        component={LedgerListScreen}
        options={{ title: "Ledgers" }}
      />
      <AppStack.Screen name="Vouchers" options={{ title: "Vouchers" }}>
        {(props: NativeStackScreenProps<AppStackParamList, "Vouchers">) => (
          <VoucherListScreen
            onCreate={() => props.navigation.navigate("NewVoucher")}
          />
        )}
      </AppStack.Screen>
      <AppStack.Screen name="NewVoucher" options={{ title: "New voucher" }}>
        {(props: NativeStackScreenProps<AppStackParamList, "NewVoucher">) => (
          <VoucherEntryScreen
            onCreated={() =>
              props.navigation.reset({
                index: 1,
                routes: [{ name: "Dashboard" }, { name: "Vouchers" }],
              })
            }
            onCancel={() => props.navigation.goBack()}
          />
        )}
      </AppStack.Screen>
      <AppStack.Screen
        name="TrialBalance"
        component={TrialBalanceScreen}
        options={{ title: "Trial Balance" }}
      />
      <AppStack.Screen
        name="ProfitLoss"
        component={ProfitLossScreen}
        options={{ title: "Profit & Loss" }}
      />
      <AppStack.Screen
        name="BalanceSheet"
        component={BalanceSheetScreen}
        options={{ title: "Balance Sheet" }}
      />
      <AppStack.Screen
        name="Outstanding"
        component={OutstandingScreen}
        options={{ title: "Outstanding" }}
      />
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
