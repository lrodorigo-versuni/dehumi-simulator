# Requirements Document

## Introduction

The Room Humidity Simulator models the evolution of moisture in a single enclosed room over time. The simulation couples three mechanisms: a condensing coil that removes moisture from air passing over it (dehumidification), a fan that drives a configurable volumetric airflow across that coil, and external disturbance events (people entering the room, water boiling) that inject moisture into the room air.

The model advances in discrete time steps. At each step it computes the moisture added by active disturbances, the moisture removed by the coil (bounded by the coil's condensation capacity and the air actually moved by the fan), and updates the room's absolute humidity. Relative humidity is derived from absolute humidity, room temperature, and the temperature-dependent saturation moisture content of air. Disturbances are injectable so that callers can drive the simulation with scripted or interactive scenarios.

This document defines the functional and quality requirements for the simulation engine and its configuration surface. It does not prescribe a specific programming language, user interface, or persistence mechanism; those are design concerns.

## Glossary

- **Simulator**: The top-level simulation engine that advances room state over time and coordinates the Room, Coil, Fan, and Disturbances.
- **Room**: The modeled enclosed space, characterized by a fixed volume, a temperature, and a current absolute humidity.
- **Coil**: The condensing coil that removes moisture from air passing over it, characterized by a surface temperature and a maximum condensation capacity.
- **Fan**: The component that moves air at a configurable volumetric flow rate across the Coil.
- **Disturbance**: A source of moisture added to the Room, external to the Coil and Fan (for example, a person or boiling water).
- **Person_Disturbance**: A Disturbance representing a human occupant that adds moisture at a defined rate while present.
- **Boiling_Water_Disturbance**: A Disturbance representing boiling water that adds moisture vapor at a defined rate while active.
- **Absolute_Humidity**: The mass of water vapor contained in the Room air, expressed in grams of water.
- **Specific_Humidity**: The mass of water vapor per unit mass or volume of air, used to express concentration independent of Room volume.
- **Relative_Humidity**: The ratio of the Room's current water vapor content to the maximum the air can hold at the Room temperature, expressed as a percentage from 0.0 to 100.0.
- **Saturation_Moisture_Content**: The maximum mass of water vapor the Room air can hold at a given temperature before condensation occurs.
- **Volumetric_Flow_Rate**: The volume of air the Fan moves per unit time, expressed in cubic meters per second.
- **Condensation_Capacity**: The maximum mass of water the Coil can remove per unit time, expressed in grams per second.
- **Coil_Effectiveness**: The fraction, from 0.0 to 1.0 inclusive, of the air moved across the Coil that is brought to saturation at the Coil surface temperature (the contact factor, equal to one minus the bypass factor).
- **Coil_Outlet_Humidity**: The Specific_Humidity, measured at the Room temperature, of air saturated at the Coil surface temperature; the lowest Specific_Humidity the Coil can bring Room air down to.
- **Moisture_Rate**: The mass of water a Disturbance adds per unit time, expressed in grams per second.
- **Time_Step**: The fixed duration of simulation advanced in one update cycle, expressed in seconds.
- **Simulation_Time**: The total elapsed simulated time since the start of a simulation run, expressed in seconds.
- **Room_Volume_Bounds**: The permitted range for a Room volume, from 0.01 to 100,000 cubic meters inclusive.
- **Room_Temperature_Bounds**: The permitted range for a Room temperature, from -50 to 100 degrees Celsius inclusive.
- **Initial_Humidity_Bounds**: The permitted range for a Room's initial Absolute_Humidity, from 0.0 to 1,000 grams per cubic meter inclusive.
- **Flow_Rate_Bounds**: The permitted range for the Fan Volumetric_Flow_Rate, from 0.0 to 10.0 cubic meters per second inclusive.
- **Moisture_Rate_Bounds**: The permitted range for a Disturbance Moisture_Rate, from 0.0 to 1000.0 grams per second inclusive.
- **Max_Active_Disturbances**: The maximum number of Disturbances that may be active simultaneously, equal to 100.
- **Max_Time_Step_Duration**: The maximum permitted Time_Step duration, equal to 3,600 seconds.

## Requirements

### Requirement 1: Room Configuration and State

**User Story:** As a simulation user, I want to configure the room's physical properties and query its humidity state, so that I can model a specific space and observe how its moisture changes.

#### Acceptance Criteria

1. WHEN the Simulator is initialized with a room volume within the range 0.01 to 100,000 cubic meters inclusive, a room temperature within the range -50 to 100 degrees Celsius inclusive, and an initial Absolute_Humidity within the range 0.0 to 1,000 grams per cubic meter inclusive, THE Simulator SHALL create a Room with those values.
2. IF the configured room volume is less than 0.01 cubic meters, THEN THE Simulator SHALL reject the configuration, create no Room, and return a configuration error indicating that the room volume is below the minimum allowed value.
3. IF the configured room volume is greater than 100,000 cubic meters, THEN THE Simulator SHALL reject the configuration, create no Room, and return a configuration error indicating that the room volume exceeds the maximum allowed value.
4. IF the configured room temperature is less than -50 degrees Celsius or greater than 100 degrees Celsius, THEN THE Simulator SHALL reject the configuration, create no Room, and return a configuration error indicating that the room temperature is out of range.
5. IF the configured initial Absolute_Humidity is less than 0.0 grams per cubic meter, THEN THE Simulator SHALL reject the configuration, create no Room, and return a configuration error indicating that the initial Absolute_Humidity is below the minimum allowed value.
6. IF the configured initial Absolute_Humidity is greater than 1,000 grams per cubic meter, THEN THE Simulator SHALL reject the configuration, create no Room, and return a configuration error indicating that the initial Absolute_Humidity exceeds the maximum allowed value.
7. WHEN a caller queries the Room state, THE Room SHALL expose its current Absolute_Humidity in grams of water within the range 0.0 to 1,000 grams inclusive.
8. WHEN a caller queries the Room state, THE Room SHALL expose its current Relative_Humidity as a percentage between 0.0 and 100.0 inclusive.
9. WHEN a caller queries the Room state, THE Room SHALL expose its current temperature in degrees Celsius within the range -50 to 100 degrees Celsius inclusive.

### Requirement 2: Humidity Representation and Derivation

**User Story:** As a simulation user, I want relative humidity derived from absolute humidity and temperature, so that the reported humidity accounts for the air's temperature-dependent capacity to hold moisture.

#### Acceptance Criteria

1. THE Simulator SHALL compute Relative_Humidity as the ratio of the Room's Absolute_Humidity to the Saturation_Moisture_Content at the Room temperature, expressed as a percentage rounded to one decimal place.
2. WHEN the Room temperature changes, THE Simulator SHALL recompute the Saturation_Moisture_Content for the new temperature before deriving Relative_Humidity.
3. IF the Room's Absolute_Humidity exceeds the Saturation_Moisture_Content at the Room temperature, THEN THE Simulator SHALL cap the Absolute_Humidity at the Saturation_Moisture_Content and report Relative_Humidity as 100.0 percent.
4. THE Simulator SHALL compute the Saturation_Moisture_Content as a function of the Room temperature.
5. THE Simulator SHALL express Absolute_Humidity in grams of water, constrained to the range 0.0 to 999.9 grams inclusive, and Relative_Humidity as a percentage between 0.0 and 100.0 inclusive.
6. IF the Room's Absolute_Humidity is 0.0 grams, THEN THE Simulator SHALL report Relative_Humidity as 0.0 percent.
7. IF the Saturation_Moisture_Content at the Room temperature is 0.0 grams, THEN THE Simulator SHALL report Relative_Humidity as 0.0 percent and SHALL provide an indication that Relative_Humidity could not be derived from a zero moisture capacity.

### Requirement 3: Fan Airflow

**User Story:** As a simulation user, I want to configure the fan's airflow rate, so that I can control how much room air is driven across the condensing coil per time step.

#### Acceptance Criteria

1. WHEN the Simulator is initialized with a Volumetric_Flow_Rate for the Fan that is greater than or equal to 0.0 and less than or equal to 10.0 cubic meters per second, THE Simulator SHALL create a Fan with that flow rate.
2. IF the configured Volumetric_Flow_Rate is less than 0.0 cubic meters per second, THEN THE Simulator SHALL reject the configuration, retain the previous Fan configuration unchanged, and return a configuration error indicating that the flow rate is below the minimum allowed value.
3. IF the configured Volumetric_Flow_Rate is greater than 10.0 cubic meters per second, THEN THE Simulator SHALL reject the configuration, retain the previous Fan configuration unchanged, and return a configuration error indicating that the flow rate exceeds the maximum allowed value.
4. IF the configured Volumetric_Flow_Rate is non-numeric or undefined, THEN THE Simulator SHALL reject the configuration, retain the previous Fan configuration unchanged, and return a configuration error indicating that the flow rate is invalid.
5. WHILE the Fan is active during a Time_Step with a Time_Step duration greater than 0.0 seconds, THE Fan SHALL move a volume of air across the Coil equal to the Volumetric_Flow_Rate in cubic meters per second multiplied by the Time_Step duration in seconds.
6. WHERE the Volumetric_Flow_Rate is equal to 0.0 cubic meters per second, THE Fan SHALL move 0.0 cubic meters of air across the Coil during the Time_Step.
7. THE Fan SHALL expose its current Volumetric_Flow_Rate in cubic meters per second.

### Requirement 4: Coil Dehumidification

**User Story:** As a simulation user, I want the condensing coil to remove moisture from air the fan moves across it, so that the room can be dehumidified over time.

#### Acceptance Criteria

1. WHEN the Fan moves air across the Coil during a Time_Step, THE Coil SHALL remove water vapor at an instantaneous rate equal to the lesser of the Condensation_Capacity and the Volumetric_Flow_Rate multiplied by the Coil_Effectiveness multiplied by the difference between the Room Specific_Humidity and the Coil_Outlet_Humidity, integrated over the Time_Step while the Room air keeps recirculating across the Coil.
2. THE Coil SHALL remove moisture only from the air the Fan moves across the Coil, SHALL remove no more than the Condensation_Capacity multiplied by the Time_Step duration, and SHALL never bring the Room Specific_Humidity below the Coil_Outlet_Humidity.
3. WHILE the moved air's Specific_Humidity corresponds to a dew point strictly greater than the Coil surface temperature, THE Coil SHALL remove moisture during the Time_Step.
4. IF the dew point of the moved air's Specific_Humidity is less than or equal to the Coil surface temperature, THEN THE Coil SHALL remove 0.0 grams of moisture during the Time_Step.
5. IF the Coil surface temperature is greater than or equal to the Room temperature, THEN THE Coil SHALL remove 0.0 grams of moisture during the Time_Step.
6. WHEN the Coil removes a mass of moisture during a Time_Step, THE Simulator SHALL decrease the Room's Absolute_Humidity by exactly that removed mass.
7. WHILE the Simulator applies Coil moisture removal, THE Simulator SHALL keep the Room's Absolute_Humidity greater than or equal to 0.0 grams.
8. IF the configured Condensation_Capacity is less than 0.0 grams per second, THEN THE Simulator SHALL reject the configuration, retain the previous Coil configuration unchanged, and return a configuration error indicating that the Condensation_Capacity is below the minimum allowed value.
9. IF the configured Coil_Effectiveness is less than 0.0, greater than 1.0, or non-numeric, THEN THE Simulator SHALL reject the configuration, retain the previous Coil configuration unchanged, and return a configuration error indicating that the Coil_Effectiveness is out of range.
10. WHEN the Room Specific_Humidity increases, or the Coil surface temperature decreases, with all other inputs unchanged, THE Coil SHALL remove a mass of moisture during the Time_Step that is greater than or equal to the mass removed before the change.

### Requirement 5: Moisture Disturbances

**User Story:** As a simulation user, I want to add moisture-generating disturbances such as people and boiling water, so that I can model realistic external humidity loads on the room.

#### Acceptance Criteria

1. WHERE a Person_Disturbance is active during a Time_Step with a Moisture_Rate within the range 0.0 to 1000.0 grams per second inclusive, THE Simulator SHALL increase the Room's Absolute_Humidity by the Person_Disturbance Moisture_Rate in grams per second multiplied by the Time_Step duration in seconds.
2. WHERE a Boiling_Water_Disturbance is active during a Time_Step with a Moisture_Rate within the range 0.0 to 1000.0 grams per second inclusive, THE Simulator SHALL increase the Room's Absolute_Humidity by the Boiling_Water_Disturbance Moisture_Rate in grams per second multiplied by the Time_Step duration in seconds.
3. WHEN a caller adds a Disturbance to the Simulator while fewer than 100 Disturbances are active, THE Simulator SHALL include that Disturbance in moisture calculations starting with the next Time_Step.
4. WHEN a caller removes an active Disturbance from the Simulator, THE Simulator SHALL exclude that Disturbance from moisture calculations starting with the next Time_Step.
5. WHERE multiple Disturbances are active during the same Time_Step, THE Simulator SHALL set the total disturbance contribution equal to the arithmetic sum of the moisture mass in grams contributed by every active Disturbance for that Time_Step.
6. IF a caller adds a Disturbance while 100 Disturbances are already active, THEN THE Simulator SHALL reject the addition, retain the current set of active Disturbances unchanged, and return a configuration error indicating that the maximum number of active Disturbances has been reached.
7. IF a Disturbance Moisture_Rate is less than 0.0 grams per second or greater than 1000.0 grams per second, THEN THE Simulator SHALL reject the Disturbance, retain the previous valid room state, and return a configuration error indicating that the Moisture_Rate is out of range.
8. IF a caller requests removal of a Disturbance that is not currently active, THEN THE Simulator SHALL retain the current set of active Disturbances unchanged and return an error indicating that the Disturbance was not found.
9. IF the total disturbance contribution for a Time_Step would raise the Room's Absolute_Humidity above the Saturation_Moisture_Content at the Room temperature, THEN THE Simulator SHALL cap the Room's Absolute_Humidity at the Saturation_Moisture_Content.

### Requirement 6: Injectable Disturbances

**User Story:** As a developer, I want disturbances to be injectable through a common interface, so that I can script scenarios or add new disturbance types without modifying the simulation engine.

#### Acceptance Criteria

1. THE Simulator SHALL accept any Disturbance that conforms to a common Disturbance interface.
2. WHEN the Simulator requests a Disturbance's contribution for a Time_Step duration greater than 0 seconds, THE Disturbance interface SHALL return the moisture mass in grams that the Disturbance contributes over that duration.
3. WHEN a caller provides a custom Disturbance conforming to the Disturbance interface, THE Simulator SHALL incorporate its moisture contribution without requiring changes to the Coil or Fan.
4. WHILE one or more Disturbances are active, THE Simulator SHALL compute the total disturbance moisture contribution for each Time_Step as the arithmetic sum of the moisture mass in grams returned by every active Disturbance.
5. THE Simulator SHALL support between 0 and 100 active Disturbances simultaneously.
6. IF an active Disturbance returns a moisture mass that is negative, NaN, or non-numeric, THEN THE Simulator SHALL exclude that Disturbance's contribution from the Time_Step total, retain the previous valid room state, and produce an error indication identifying the offending Disturbance.

### Requirement 7: Time-Stepped Simulation Advance

**User Story:** As a simulation user, I want to advance the simulation in discrete time steps, so that I can observe how room humidity evolves over time.

#### Acceptance Criteria

1. WHEN a caller advances the Simulator by one Time_Step, THE Simulator SHALL apply all active Disturbance moisture additions, then apply Coil moisture removal, then update the Room's Absolute_Humidity for that Time_Step.
2. WHEN the Simulator completes a Time_Step, THE Simulator SHALL increase the Simulation_Time by the Time_Step duration.
3. IF the configured Time_Step duration is less than or equal to 0.0 seconds or greater than 3,600 seconds, THEN THE Simulator SHALL reject the configuration, retain the previous Time_Step duration unchanged, and return a configuration error indicating that the Time_Step duration is out of range.
4. WHILE the Simulator advances Time_Steps, THE Simulator SHALL clamp any computed Absolute_Humidity value below 0.0 grams to 0.0 grams so that the Room's Absolute_Humidity remains greater than or equal to 0.0 grams.
5. WHEN the Simulator advances a Time_Step, THE Simulator SHALL expose the Room's updated Absolute_Humidity in grams and updated Relative_Humidity as a percentage between 0.0 and 100.0 inclusive after the step completes.
6. WHEN a caller advances the Simulator by a count of Time_Steps that is greater than or equal to 1, THE Simulator SHALL apply that number of sequential Time_Step updates in order.
7. IF a caller advances the Simulator by a count of Time_Steps that is less than 1, THEN THE Simulator SHALL reject the request, leave the Simulation_Time and the Room's Absolute_Humidity unchanged, and return an input error indicating that the Time_Step count is invalid.

## Phase 2: Compressor-Driven Energy-Consumption Model

> **Increment note:** The requirements in this section define a later increment that adds an optional Compressor and an energy-consumption model on top of the Phase 1 simulation. They extend, and do not replace, Requirements 1 through 7. When no Compressor is configured, the Simulator behaves exactly as specified in Phase 1. The requirements below continue the numbering from the Phase 1 sequence, starting at Requirement 8.

### Additional Glossary Terms

- **Compressor**: An optional, injectable component that actively cools the Coil by driving the Coil surface temperature toward a configured Setpoint_Temperature while running, and whose operation consumes electrical energy.
- **COP**: The Compressor coefficient of performance, a dimensionless positive ratio of heat extracted to electrical energy consumed, used to convert extracted heat into electrical energy.
- **COP_Table**: A lookup table giving the COP as a function of the Coil surface temperature and, optionally, the Room temperature, interpolated linearly (one dimension) or bilinearly (two dimensions) between tabulated points and held at the edge values outside them.
- **Max_Compressor_Power**: The maximum electrical power in watts the Compressor can absorb, strictly greater than 0.0 (for example 400 W). The cooling the Compressor can deliver to the Coil is limited to the COP multiplied by the Max_Compressor_Power.
- **Coil_Heat_Load**: The heat in watts that the moved air would release to the Coil at a given Coil surface temperature: the sensible heat of cooling the moved air plus the latent heat of the moisture condensing at the instantaneous removal rate.
- **COP_Bounds**: The permitted range for a Compressor COP, strictly greater than 0.0 with an upper limit of 20.0 inclusive.
- **Setpoint_Temperature**: The Coil surface temperature in degrees Celsius that the Compressor maintains while running, within the range -50 to 100 degrees Celsius inclusive.
- **Compressor_State**: The operational condition of the Compressor during a Time_Step, exactly one of running or off. The initial Compressor_State upon creation is off.
- **Heat_Extracted**: The thermal energy in joules the Coil removes from the moved air during a Time_Step, comprising the latent heat released by condensing moisture plus the sensible heat removed by cooling the moved air.
- **Latent_Heat_Of_Condensation**: The constant thermal energy released when water vapor condenses to liquid, equal to 2,260 joules per gram of water condensed.
- **Density_Of_Air**: The constant mass of air per unit volume, equal to 1.2 kilograms per cubic meter.
- **Specific_Heat_Of_Air**: The constant thermal energy required to raise the temperature of one kilogram of air by one degree Celsius, equal to 1,005 joules per kilogram per degree Celsius.
- **Energy_Consumed**: The cumulative electrical energy the Compressor has consumed since the start of a simulation run, expressed in joules.
- **Power_Draw**: The instantaneous electrical power the Compressor draws during the most recently completed Time_Step, expressed in watts.
- **Coil_Drift_Behavior**: The behavior whereby, while the Compressor is off, the Coil surface temperature is set equal to the Room temperature and no active cooling is applied.

### Requirement 8: Compressor Configuration and Validation

**User Story:** As a simulation user, I want to configure an optional compressor with a coefficient of performance and a target coil setpoint, so that I can model active cooling and its energy cost.

#### Acceptance Criteria

1. WHEN the Simulator is initialized with a Compressor whose COP is strictly greater than 0.0 and less than or equal to 20.0 and whose Setpoint_Temperature is within the range -50 to 100 degrees Celsius inclusive, THE Simulator SHALL create a Compressor with those values.
2. IF the configured COP is less than or equal to 0.0, THEN THE Simulator SHALL reject the Compressor configuration, retain the previous valid state, and return a configuration error indicating that the COP must be greater than 0.0.
3. IF the configured COP is greater than 20.0, THEN THE Simulator SHALL reject the Compressor configuration, retain the previous valid state, and return a configuration error indicating that the COP exceeds the maximum allowed value.
4. IF the configured COP is non-numeric or undefined, THEN THE Simulator SHALL reject the Compressor configuration, retain the previous valid state, and return a configuration error indicating that the COP is invalid.
5. IF the configured Setpoint_Temperature is less than -50 degrees Celsius or greater than 100 degrees Celsius, THEN THE Simulator SHALL reject the Compressor configuration, retain the previous valid state, and return a configuration error indicating that the Setpoint_Temperature is out of range.
6. IF the configured Setpoint_Temperature is non-numeric or undefined, THEN THE Simulator SHALL reject the Compressor configuration, retain the previous valid state, and return a configuration error indicating that the Setpoint_Temperature is invalid.
7. WHEN a caller queries the Compressor configuration, THE Compressor SHALL expose its current COP as a dimensionless value strictly greater than 0.0 and less than or equal to 20.0 and its current Setpoint_Temperature in degrees Celsius within the range -50 to 100 degrees Celsius inclusive.
8. WHEN a Compressor is created, THE Simulator SHALL set the initial Compressor_State to off.
9. THE Compressor SHALL expose its current Compressor_State as exactly one of running or off.
10. IF the configured Max_Compressor_Power is less than or equal to 0.0 watts or is non-numeric, THEN THE Simulator SHALL reject the Compressor configuration, retain the previous valid state, and return a configuration error indicating that the maximum power must be greater than 0.0 or is invalid.
11. WHERE the Compressor is configured with a COP_Table instead of a fixed COP, THE Simulator SHALL use the COP interpolated at the current Coil surface temperature and Room temperature wherever this document uses the COP, and IF the COP_Table has an empty or non-increasing temperature axis, non-finite temperatures, a shape that does not match its axes, or any COP outside COP_Bounds, THEN THE Simulator SHALL reject the Compressor configuration, retain the previous valid state, and return a configuration error indicating that the COP table is invalid and why.

### Requirement 9: Compressor Coupling to Coil Surface Temperature

**User Story:** As a simulation user, I want the compressor to control the coil surface temperature, so that running the compressor enables dehumidification and turning it off stops active cooling.

#### Acceptance Criteria

1. WHILE the Compressor is configured and the Compressor_State is running during a Time_Step and the Coil_Heat_Load at the Setpoint_Temperature does not exceed the COP multiplied by the Max_Compressor_Power, THE Simulator SHALL set the Coil surface temperature to the Compressor Setpoint_Temperature in degrees Celsius.
2. WHILE the Compressor is configured and the Compressor_State is off during a Time_Step, THE Simulator SHALL set the Coil surface temperature equal to the Room temperature in degrees Celsius for that Time_Step and apply no active cooling.
3. IF the Compressor is configured and the Compressor_State is off during a Time_Step, THEN THE Coil SHALL remove 0.0 grams of moisture during that Time_Step in accordance with Requirement 4.
4. WHEN the Compressor is configured and the Compressor_State is running during a Time_Step, THE Simulator SHALL apply Coil moisture removal for that Time_Step using the Coil surface temperature set to the Setpoint_Temperature, in accordance with Requirement 4.
5. WHEN a caller sets the Compressor_State to running or off, THE Simulator SHALL leave the current Time_Step's Compressor_State unchanged and apply the new Compressor_State starting with the next Time_Step.
6. WHILE the Compressor is configured and running and the Coil_Heat_Load at the Setpoint_Temperature exceeds the COP multiplied by the Max_Compressor_Power, THE Simulator SHALL set the Coil surface temperature to the temperature between the Setpoint_Temperature and the Room temperature at which the Coil_Heat_Load equals the COP multiplied by the Max_Compressor_Power, re-evaluating it as the Room humidity changes within the Time_Step, and SHALL report that the Compressor is power-limited.

### Requirement 10: COP-Based Energy Consumption Per Time Step

**User Story:** As a simulation user, I want energy consumption computed from the heat the coil removes and the compressor's coefficient of performance, so that the model reflects the physical cost of dehumidification.

#### Acceptance Criteria

1. WHEN the Compressor is configured and the Compressor_State is running during a Time_Step, THE Simulator SHALL compute the Heat_Extracted for that Time_Step in joules as the sum of the latent heat component and the sensible heat component, constrained to a value greater than or equal to 0.0 joules.
2. THE Simulator SHALL compute the latent heat component of Heat_Extracted in joules as the mass in grams of moisture the Coil removes during the Time_Step multiplied by the Latent_Heat_Of_Condensation of 2,260 joules per gram.
3. WHEN the Compressor is configured and running during a Time_Step, the volume of air the Fan moves across the Coil during that Time_Step is greater than 0.0 cubic meters, and the Room temperature is strictly greater than the Coil surface temperature, THE Simulator SHALL compute the sensible heat component of Heat_Extracted in joules as the moved-air volume in cubic meters multiplied by the Density_Of_Air of 1.2 kilograms per cubic meter, multiplied by the Specific_Heat_Of_Air of 1,005 joules per kilogram per degree Celsius, multiplied by the Coil_Effectiveness, multiplied by the difference in degrees Celsius of the Room temperature minus the Coil surface temperature.
4. IF the Room temperature is less than or equal to the Coil surface temperature during a Time_Step, THEN THE Simulator SHALL set the sensible heat component to 0.0 joules for that Time_Step.
5. WHERE the volume of air the Fan moves across the Coil during a Time_Step is equal to 0.0 cubic meters, THE Simulator SHALL set the sensible heat component to 0.0 joules and compute the Heat_Extracted for that Time_Step from the latent heat component alone.
6. WHEN the Compressor is configured and running during a Time_Step, THE Simulator SHALL compute the electrical energy consumed for that Time_Step in joules as the Heat_Extracted in joules divided by the Compressor COP, where the Compressor COP is strictly greater than 0.0 as guaranteed by Requirement 8, yielding a value greater than or equal to 0.0 joules.
7. WHILE the Compressor_State is off during a Time_Step, THE Simulator SHALL compute the electrical energy consumed for that Time_Step as 0.0 joules.
8. WHEN the Compressor is configured and running during a Time_Step and the Coil removes 0.0 grams of moisture and the sensible heat component is 0.0 joules, THE Simulator SHALL compute the electrical energy consumed for that Time_Step as 0.0 joules.

### Requirement 11: Cumulative Energy Accounting and Power-Draw Exposure

**User Story:** As a simulation user, I want to track total energy consumed and the current power draw, so that I can evaluate the energy efficiency of a dehumidification scenario.

#### Acceptance Criteria

1. WHEN the Simulator completes a Time_Step while a Compressor is configured, THE Simulator SHALL increase the Energy_Consumed by exactly the electrical energy consumed during that Time_Step as computed in Requirement 10, which is 0.0 joules when the Compressor_State is off.
2. WHILE a Compressor is configured and the Simulator advances Time_Steps, THE Simulator SHALL keep the Energy_Consumed monotonically non-decreasing, never reducing it below its value after the previous Time_Step.
3. WHEN a caller queries the Simulator after a completed Time_Step, THE Simulator SHALL expose the Energy_Consumed in joules as a value greater than or equal to 0.0.
4. WHEN the Simulator completes a Time_Step with a Compressor configured and a Time_Step duration greater than 0.0 seconds, THE Simulator SHALL set the Power_Draw to the electrical energy consumed during that Time_Step in joules, as computed in Requirement 10, divided by the Time_Step duration in seconds.
5. WHEN a caller queries the Simulator after a completed Time_Step, THE Simulator SHALL expose the Power_Draw in watts as the value computed for the most recently completed Time_Step, greater than or equal to 0.0.
6. WHEN a simulation run starts, THE Simulator SHALL set the Energy_Consumed to 0.0 joules and the Power_Draw to 0.0 watts before the first Time_Step is advanced.
7. WHILE the Compressor_State is off during a Time_Step, THE Simulator SHALL set the Power_Draw to 0.0 watts for that Time_Step and leave the Energy_Consumed unchanged from its value after the previous Time_Step.
8. WHILE the Compressor_State is running during a Time_Step for which the electrical energy consumed is 0.0 joules, THE Simulator SHALL set the Power_Draw to 0.0 watts for that Time_Step.
9. WHILE the Compressor is running, THE Simulator SHALL keep the electrical energy consumed during each Time_Step less than or equal to the Max_Compressor_Power multiplied by the Time_Step duration, so that the Power_Draw never exceeds the Max_Compressor_Power.

### Requirement 12: Backward Compatibility Without a Compressor

**User Story:** As an existing simulation user, I want the simulation to behave exactly as before when no compressor is configured, so that adopting the energy model does not change my existing scenarios.

#### Acceptance Criteria

1. WHILE no Compressor is configured, THE Simulator SHALL hold the Coil surface temperature at its configured constant value and SHALL apply no active cooling to the Coil during any Time_Step, in accordance with the Phase 1 behavior.
2. WHILE no Compressor is configured, THE Simulator SHALL apply Coil moisture removal, Disturbance moisture additions, and Time_Step advancement exactly as specified in Requirements 1 through 7.
3. WHILE no Compressor is configured, after each completed Time_Step THE Simulator SHALL report the Energy_Consumed as 0.0 joules and the Power_Draw as 0.0 watts.
4. WHEN a caller runs any scenario with no Compressor configured using a given Room configuration, Fan configuration, Coil configuration, Disturbance set, and sequence of Time_Step advances, THE Simulator SHALL expose, after each Time_Step, a Room Absolute_Humidity in grams and Relative_Humidity as a percentage that each equal the corresponding Phase 1 output for the same configuration and sequence to within an absolute tolerance of 0.1 grams for Absolute_Humidity and 0.1 percentage points for Relative_Humidity.
5. WHEN a Compressor is added to a Simulator that previously had none, THE Simulator SHALL retain the current Room Absolute_Humidity, Room temperature, and Simulation_Time unchanged at the moment of addition, and SHALL begin applying Compressor coupling and energy accounting starting with the next Time_Step with the Energy_Consumed continuing from its current value.

## Phase 3: Closed-Loop Control

> **Increment note:** Requirements 13-15 let a user-supplied control algorithm drive the fan speed and an on/off Compressor, so the algorithm's performance can be measured. They extend Requirements 1-12.

### Requirement 13: Runtime Actuators

1. WHEN a caller sets a new fan Volumetric_Flow_Rate during a run, THE Simulator SHALL validate it as in Requirement 3 and apply it starting with the next Time_Step, retaining the current Fan on error.
2. THE closed loop SHALL offer the controller a fixed list of fan speeds, each with a flow rate and an electrical power, and SHALL count the fan's electrical energy in the energy KPIs.

### Requirement 14: Controller Interface, Sensors and Protections

1. AT every Time_Step THE closed loop SHALL call the controller with the measured relative humidity, room and coil temperature, actual compressor and fan state, compressor power, whether the compressor may switch, and the target relative humidity, and SHALL apply the returned compressor on/off request and fan speed index.
2. IF the controller returns anything other than a valid command (unknown type or fan speed index out of range), THEN THE closed loop SHALL stop with an error identifying the problem.
3. THE closed loop SHALL model the relative-humidity sensor with configurable Gaussian noise, resolution and first-order time constant, reproducible from a seed; KPIs SHALL use the true relative humidity.
4. THE closed loop SHALL refuse compressor changes before the minimum on time or minimum off time has elapsed, SHALL refuse a compressor start without airflow, SHALL keep the fan at least at its lowest running speed while the compressor runs, and SHALL count every refused or overridden request.

### Requirement 15: Performance Indicators

1. AFTER a run THE closed loop SHALL report time in, above and below the comfort band, mean and maximum relative humidity, RMS error against the target, relative-humidity excess above the band in %·h, total, compressor and fan energy, water removed and water removed per kWh, compressor starts and starts per hour, compressor-on and fan-only time, and blocked commands.

### Requirement 16: Open-Window Air Exchange

1. WHEN an open window is active during a Time_Step, THE Simulator SHALL change the Room's Absolute_Humidity by the volume times the difference between the outdoor humidity (outdoor vapor pressure expressed at the Room temperature) and the Room Specific_Humidity, times one minus the exponential of minus the window airflow times the Time_Step duration divided by the Room volume, a mass that may be negative.
2. THE Simulator SHALL accept a negative contribution only from a Disturbance that computes an air exchange from the Room state; a negative contribution from any other Disturbance SHALL remain invalid per Requirement 6.6.
3. THE window airflow SHALL vary as the mean airflow times the maximum of zero and one plus an Ornstein-Uhlenbeck process with configurable relative standard deviation and correlation time, reproducible from a seed and independent of the controller's actions.
4. IF the window airflow is outside 0 to 10,000 m³/h, the outdoor temperature outside -50 to 60 °C, the outdoor relative humidity outside 0 to 100 %, the gustiness outside 0 to 2, or the gust time not greater than 0, THEN THE Simulator SHALL reject the window with a field-specific error and keep the active set unchanged.

### Requirement 17: Frost and Defrost

1. WHILE the compressor runs with the Coil surface temperature below 0 °C, THE Simulator SHALL accumulate the removed water as frost on the Coil and SHALL add the latent heat of fusion of 334 J per gram to the Heat_Extracted.
2. THE Coil effectiveness SHALL be divided by one plus the frost mass over a configurable frost-halving mass, for both moisture removal and sensible heat.
3. WHEN the Coil stays at or below the defrost start temperature (default -1 °C) for the defrost delay (default 300 s) while running, THE Simulator SHALL start a defrost: the compressor SHALL not run whatever state is requested, and the defrost SHALL end when, after the minimum duration (default 120 s), the Coil is above the end temperature (default +3 °C), or at the maximum duration (default 900 s).
4. WHILE the compressor does not run and frost remains, THE Coil SHALL stay at 0 °C, remove no moisture, and melt frost with the sensible heat of the moved air plus a still-air term; a configurable fraction (default 30 %) of the melted water SHALL return to the Room air.
5. WHERE defrost is disabled, THE Simulator SHALL behave as in Requirements 8-12.
6. THE closed loop SHALL tell the controller whether the unit is defrosting, SHALL keep the fan at least at its lowest running speed during defrost, and SHALL report defrost cycles, time defrosting and maximum frost.

### Requirement 18: On/Off Compressor and Coil Thermal Balance

1. WHERE the Compressor is configured as on/off (the default), WHILE it runs THE Compressor SHALL absorb exactly its Max_Compressor_Power and deliver COP(T_coil) times that power as cooling; no Setpoint_Temperature SHALL be applied.
2. WHERE the Compressor is on/off, THE Coil surface temperature SHALL be a state with a configurable heat capacity, evolving as C·dT/dt = signed sensible exchange with the moved air + latent heat of the water condensing (or freezing) on the Coil + still-air exchange − compressor cooling, integrated so that the result is exact for a linear balance.
3. WHILE frost remains on the Coil, THE Coil SHALL not warm above 0 °C; the surplus heat SHALL melt the frost at 334 J/g.
4. THE Coil surface temperature of an on/off unit SHALL never exceed the Room temperature.
5. IF an on/off Compressor is configured with an infinite maximum power, a non-positive coil heat capacity, a negative still-air coupling, or an unknown mode, THEN THE Simulator SHALL reject the configuration with a field-specific error.
6. WHERE the Compressor is configured as modulating, THE Simulator SHALL behave as in Requirements 9-11 and 17.
