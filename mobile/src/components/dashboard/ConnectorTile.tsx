import React from "react";

import { DashboardConnector } from "../../api/dashboard";
import Tile from "./Tile";

export default function ConnectorTile({
  connector,
}: {
  connector: DashboardConnector;
}): React.ReactElement {
  const connected = connector.connected;
  const tally = connector.tally_running;

  let subtitle: string;
  let tone: "good" | "warn" | "bad";
  if (!connected) {
    tone = "bad";
    if (connector.last_seen_seconds_ago !== null) {
      subtitle = `Last seen ${formatAgo(connector.last_seen_seconds_ago)} ago`;
    } else {
      subtitle = "Connector never enrolled";
    }
  } else if (tally === false) {
    tone = "warn";
    subtitle = "Tally not running on the connector host";
  } else {
    tone = "good";
    subtitle = tally === true ? "Tally is running" : "Tally status unknown";
  }

  return (
    <Tile
      label="TALLY CONNECTOR"
      primary={connected ? "Connected" : "Disconnected"}
      subtitle={subtitle}
      tone={tone}
      accessibilityLabel="tile-connector"
    />
  );
}

function formatAgo(seconds: number): string {
  if (seconds < 60) return `${seconds}s`;
  const minutes = Math.floor(seconds / 60);
  if (minutes < 60) return `${minutes}m`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `${hours}h`;
  return `${Math.floor(hours / 24)}d`;
}
