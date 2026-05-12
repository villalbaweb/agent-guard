declare module 'react-cytoscapejs' {
  import { Component } from 'react';
  import cytoscape from 'cytoscape';

  interface CytoscapeComponentProps {
    elements: any[];
    stylesheet?: cytoscape.StylesheetStyle[];
    layout?: cytoscape.LayoutOptions;
    style?: React.CSSProperties;
    pan?: { x: number; y: number };
    zoom?: number;
    minZoom?: number;
    maxZoom?: number;
    zoomingEnabled?: boolean;
    userZoomingEnabled?: boolean;
    panningEnabled?: boolean;
    userPanningEnabled?: boolean;
    boxSelectionEnabled?: boolean;
    autoungrabify?: boolean;
    autounselectify?: boolean;
    cy?: (cy: cytoscape.Core) => void;
    id?: string;
    className?: string;
  }

  export default class CytoscapeComponent extends Component<CytoscapeComponentProps> {}
}
