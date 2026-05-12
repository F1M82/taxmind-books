import React from "react";

import { DashboardGstLiability } from "../../api/dashboard";
import { formatINR } from "../../utils/money";
import Tile from "./Tile";

export default function GstTile({
  gst,
}: {
  gst: DashboardGstLiability;
}): React.ReactElement {
  return (
    <Tile
      label="GST LIABILITY (MTD)"
      primary={formatINR(gst.month_to_date)}
      subtitle="Indicative — output GST minus input GST"
      accessibilityLabel="tile-gst"
    />
  );
}
