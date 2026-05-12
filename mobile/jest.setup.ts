/**
 * Jest setup for the mobile project.
 *
 * @react-native-async-storage/async-storage ships a native module
 * that throws on import in jest-expo because the native side isn't
 * available. Registering the official in-memory mock here makes any
 * test that transitively imports the library (e.g. via
 * src/api/client.ts) load cleanly without each test having to
 * declare its own jest.mock(). See:
 *   https://react-native-async-storage.github.io/async-storage/docs/advanced/jest
 */
jest.mock("@react-native-async-storage/async-storage", () =>
  require("@react-native-async-storage/async-storage/jest/async-storage-mock"),
);
