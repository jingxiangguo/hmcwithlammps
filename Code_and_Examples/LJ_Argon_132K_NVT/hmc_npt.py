from ctypes import *        
import sys,random,math
from lammps import lammps

#-------------------- Run Parameters --------------------#

# Thermodynamic state -  configured for LAMMPS' "real" units
T = 132.0000             # Temperature [K]
P = 29.60770             # Pressure [atm]

# MC parameters
n_sweeps  = 20000000     # Number of MC Sweeps (1 Sweep = 1 HMC or Volume Move)
n_steps = 10             # Number of MD Steps per HMC trial
dt = 30.0                # time step [fs]
p_hmc = 1.1              # Probability of selecting HMC move (vs Volume Move); >= 1.0 for NVT ensemble      
lnvol_max = 0.04         # Maximum log volume displacement
rseed = 1234             # Random number seed (fixed for testing)

# File output frequency
freq_thermo = 50         # Thermodynamic output
freq_traj = 5000         # XYZ trajectory
freq_restart = 5000      # Restart file (LAMMPS write_data)
freq_flush = 500         # Flush files


#-------------------- Physical Constants and Conversion Factors --------------------#
# Note: These should changes if LAMMPS' units change

# Constants.
kB = 1.380648520000e-23  # Boltzmann's constant [m^2 kg s^-2 K^-1]
Nav = 6.02214090000e23   # Avogadro's number [molecules mol^-1]
R = kB * Nav / 1000.0    # Gas constant [kJ mol^-1 K^-1]

# Thermal energy 
kTs = R*T                # SI units: [kJ mol^-1]
kTL = kTs/4.184          # LAMMPS units [real units, kcal mol^-1] 

# Velocity prefactor 
vf = 1e-4*R*T            # LAMMPS units [real units, g A^2 * fs^-2 mol^-1]            

# Pressure prefactor for volume change move
Pb = P*1.01325           # Pressure [bar]
Pc = kB*1.0e30*1.0e-5    # Conversion factor [bar A^3 K^-1]
Pf = Pb/(Pc*T)           # Prefactor for Metropolis criterion [A^-3]


#-------------------- Initialization --------------------#

# Seed random number generator
random.seed(rseed)

# Initialize LAMMPS
lmp = lammps(name="",cmdargs=["-log","none","-screen","none"])

# Load script/data
lmp.file("in.nve")

# Set the timestep
lmp.command("timestep %f" % dt)

# Define compute for kinetic energy and virial pressure
lmp.command("compute thermo_ke all ke")

# Get initial system properties
natoms = lmp.extract_global("natoms",0)
mass = lmp.extract_atom("mass",2)
atomid = lmp.gather_atoms("id",0,1)
atype = lmp.gather_atoms("type",0,1)

# Allocate coordinate and velocity arrays 
x=(3*natoms*c_double)()
x_new = (3*natoms*c_double)()
v=(3*natoms*c_double)()

# Initialize properties
pe = 0.0
ke = 0.0
etot = 0.0
box = 0.0
vol = 0.0

# Initialize counters
n_acc_hmc = 0.0
n_try_hmc = 0.0
n_acc_vol = 0.0
n_try_vol = 0.0

# Get initial position and velocities
x = lmp.gather_atoms("x",1,3)
v = lmp.gather_atoms("v",1,3)

# Compute initial PE [dimensionless]
pe = lmp.extract_compute("thermo_pe",None,0)/kTL

# Compute box length and volume (assumes cubic!)
boxlo = lmp.extract_global("boxxlo",1)
boxhi = lmp.extract_global("boxxhi",1)
box = boxhi- boxlo
vol = math.pow(box,3)

# Open files for writing
thermo = open('thermo.dat', 'w')
traj = open('traj.dat', 'w')


#-------------------- Support Functions --------------------#

# Velocity initialization
# -Draw initial velocities from Maxwell-Boltzmann distribution
# -Send velocities to LAMMPS
# -"Run 0" to set velocities internally
# WARNING: THIS ROUTINE ONLY WORKS FOR POINT PARTICLES.  DO NOT USE FOR RIGID BODIES.
def init_vel():
  for i in range(natoms):
    indx = 3*i
    sigma = math.sqrt(vf/mass[atype[i]])
    v[indx] =  random.gauss(0.0,sigma) 
    v[indx+1] = random.gauss(0.0,sigma)
    v[indx+2] = random.gauss(0.0,sigma)
  lmp.scatter_atoms("v",1,3,v)
  lmp.command("run 0")
  return

# Computes MC move acceptance ratio
def acc_ratio(acc,trys):
  if(trys == 0.0):
    ratio = 0.0
  else:
    ratio = acc/trys
  return ratio

#-------------------- MC --------------------#

for isweep in range(n_sweeps):

  # HMC Move
  if(random.random() <= p_hmc): 

    # Update number of trial 
    n_try_hmc += 1.0

    # Scatter coordinates 
    lmp.scatter_atoms("x",1,3,x)

    # Generate initial velocities; compute KE [dimensionless]
    init_vel()
    ke = lmp.extract_compute("thermo_ke",None,0)/kTL
    etot = pe + ke
   
    # Run n_steps MD steps
    lmp.command("run %d" % n_steps)
   
    # Compute new PE, KE, and total energy [dimensionless]
    pe_new = lmp.extract_compute("thermo_pe",None,0)/kTL
    ke_new = lmp.extract_compute("thermo_ke",None,0)/kTL
    etot_new = pe_new + ke_new

    # Compute dH, the argument for the acceptance criterion [dimensionless]
    dH = (etot_new - etot)
   
    # Apply Metropolis acceptance criterion
    if random.random() <= math.exp(-dH):
      n_acc_hmc += 1
      pe = pe_new
      x_new = lmp.gather_atoms("x",1,3)
      for i in range(3*natoms):
        x[i] = x_new[i]
 
  # Volume MC Move
  else:

    # Update number of trials
    n_try_vol += 1.0

    # Scatter coordinates (not necessary here)
    lmp.scatter_atoms("x",1,3,x)

    # Compute random displacement in ln(V)
    lnvol = math.log(vol) + (random.random() - 0.5)*lnvol_max

    # Calculate new box volume, size and scale factor
    vol_new = math.exp(lnvol)
    box_new  = math.pow(vol_new, 1.0/3.0)
    lmp.command("change_box all x final 0.0 %.10f y final 0.0 %.10f z final 0.0 %.10f units box" % (box_new, box_new, box_new))
    
    # Scale the coordinates and send to LAMMPS
    scalef = box_new/box
    for i in range(3*natoms):   
      x_new[i] = scalef*x[i]
    lmp.scatter_atoms("x",1,3,x_new)
    lmp.command("run 0")

    # Compute the new PE [dimensionless]
    pe_new = lmp.extract_compute("thermo_pe",None,0)/kTL

    # Calculate argument for the acceptance criterion [dimensionless]
    arg = (pe_new-pe) + Pf*(vol_new-vol) - (float(natoms) + 1.0)*math.log(vol_new/vol)

    # Apply Metropolis acceptance criterion
    if random.random() <= math.exp(-arg):
      n_acc_vol += 1.0
      pe = pe_new
      vol = vol_new
      box = box_new
      for i in range(3*natoms):
        x[i] = x_new[i]
    else: # Reject, restore the old state
      lmp.command("change_box all x final 0.0 %.10f y final 0.0 %.10f z final 0.0 %.10f units box" % (box, box, box))      


  # File Input and Output
  if((isweep + 1) % freq_thermo == 0):  # Thermodynamic data
    hmc_acc = acc_ratio(n_acc_hmc, n_try_hmc)
    vol_acc = acc_ratio(n_acc_vol, n_try_vol)
    virial = lmp.extract_compute("thermo_press",None,0)  # Get virial pressure 
    thermo.write("%d %f %f %f %f %f %f\n" % (isweep + 1, kTL*pe, virial, vol, hmc_acc, vol_acc, dH))
  if((isweep + 1) % freq_traj == 0):    # Trajectory
    traj.write("%d \n" % natoms)
    traj.write("%.10f %.10f %.10f \n" % (box,box,box))
    for i in range(natoms):
      indx = 3*i
      traj.write("%d %.10f %.10f %.10f \n" % (atomid[i], x[indx], x[indx+1], x[indx+2]))
  if((isweep + 1) % freq_restart == 0): lmp.command("write_data restart_a.dat") # Alternate restart files for redundancy
  if((isweep + 1) % freq_restart/2 == 0): lmp.command("write_data restart_b.dat")
  if((isweep + 1) % freq_flush == 0):  # Force flush
    thermo.flush()
    traj.flush()

# Close files
thermo.close()
traj.close()

