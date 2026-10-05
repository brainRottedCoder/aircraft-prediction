// Static presentation data for the aircraft: names, 3D anchor geometry and module labels.
//
// Everything *quantitative* used to live here too — per-module degradation weights, a
// spare table with stock levels and lead times, three maintenance agencies with slot and
// turnaround days, and hardcoded technical records. All of it was fabricated, and all of
// it is now supplied by the API: /fleet/heatmap and /aircraft/{code} for health,
// /aircraft/{code}/parts/{part} for the spare, agency, records and back-in-service
// arithmetic. What remains here is only what the server has no opinion about — what the
// part is called and where it sits on the model.

export const ENG = { fan: 'Fan', hpc: 'HPC', hpt: 'HPT', lpt: 'LPT' };

// Centre of the jet model, used to centre it in the scene.
export const JC = [28.8, 6, 0];

// a/n: arrow anchor + normal, t/d: ray target + direction used to snap arrows onto the jet surface.
// subs: sub-component labels. `real` marks a part whose 3D model is a real airframe
// component rather than an illustrative stand-in.
export const PARTS = {
  eng: { name: 'Engine', a: [-32, 3, 0], n: [0, 1, 0], t: [-30, 4, 0], d: [-0.45, 0.35, 1], real: 1, subs: ['Fan', 'HPC', 'HPT', 'LPT'] },
  radar: { name: 'Radar and avionics', a: [104, -2.5, 0], n: [1, 0.45, 0.2], t: [96, 2, 0], d: [1, 0.35, 0.45], subs: ['Antenna array', 'Gimbal drive', 'Transmitter'] },
  gear: { name: 'Landing gear', a: [30, -7, 0], n: [0, -1, 0], t: [28, -6, 0], d: [0.1, -1, 0.35], subs: ['Tyres', 'Struts', 'Retract actuator'] },
  hyd: { name: 'Hydraulics', a: [-18, 0, 28], n: [0, 1, 0], t: [-8, -3, 28], d: [0, 1, 0.15], subs: ['Pump', 'Servo actuator', 'Lines'] },
  fuel: { name: 'Fuel system', a: [-2, 11, 0], n: [0, 1, 0], t: [-2, 12, 0], d: [0, 1, 0], subs: ['Tank', 'Boost pump', 'Valves'] },
};

export const KEYS = Object.keys(PARTS);
