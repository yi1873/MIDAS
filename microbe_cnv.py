#!/usr/bin/python

# MicrobeCNV - estimation of gene-copy-number from shotgun sequence data
# Copyright (C) 2015 Stephen Nayfach
# Freely distributed under the GNU General Public License (GPLv3

__version__ = '0.0.1'

# TO DO
# time each section of code
# compare counting speed to samtools
# import microbe_species module
# print cluster name, size, and relative abundance when read-mapping
# compute coverage, %id of pangenes

# Libraries
# ---------
import sys
import os
import numpy as np
import argparse
import pysam
import gzip
import time
import subprocess
import operator
import Bio.SeqIO

# Functions
# ---------
def parse_arguments():
	""" Parse command line arguments """
	
	parser = argparse.ArgumentParser(usage='%s [options]' % os.path.basename(__file__))
	
	parser.add_argument('--version', action='version', version='MicrobeCNV %s' % __version__)
	parser.add_argument('-v', '--verbose', action='store_true', default=False)
	
	input = parser.add_argument_group('Input')
	input.add_argument('-1', type=str, dest='m1', help='FASTQ file containing 1st mate')
	input.add_argument('-2', type=str, dest='m2', help='FASTQ file containing 2nd mate')
	input.add_argument('-U', type=str, dest='r', help='FASTQ file containing unpaired reads')
	input.add_argument('-D', type=str, dest='db_dir', help='Directory of bt2 indexes for genome clusters')
	
	output = parser.add_argument_group('Output')
	output.add_argument('-o', type=str, dest='out', help='Base name for output files')

	pipe = parser.add_argument_group('Pipeline')
	pipe.add_argument('--all', action='store_true', dest='all',
		default=False, help='Run entire pipeline')
	pipe.add_argument('--profile', action='store_true', dest='profile',
		default=False, help='Fast estimation of genome-cluster abundance')
	pipe.add_argument('--align', action='store_true', dest='align',
		default=False, help='Align reads to genome-clusters')
	pipe.add_argument('--map', action='store_true', dest='map',
		default=False, help='Assign reads to mapping locations')
	pipe.add_argument('--cov', action='store_true', dest='cov',
		default=False, help='Compute coverage of pangenomes')
		
	aln = parser.add_argument_group('Alignment')
	aln.add_argument('--reads', type=int, dest='reads', help='Number of reads to use from sequence file (use all)')
	aln.add_argument('--abun', type=float, dest='abun', default=0.05,
			help='Abundance threshold for aligning to genome cluster (0.05)')
			
	map = parser.add_argument_group('Mapping')
	map.add_argument('--pid', type=float, dest='pid', default=90,
			help='Minimum percent identity between read and reference (90.0)')
	
	return vars(parser.parse_args())

def check_arguments(args):
	""" Check validity of command line arguments """
	
	# Pipeline options
	if not any([args['all'], args['profile'], args['align'], args['map'], args['cov']]):
		sys.exit('Specify pipeline option(s): --all, --profile, --align, --map, --cov')
	if args['all']:
		args['profile'] = True
		args['align'] = True
		args['map'] = True
		args['cov'] = True

	# Input options
	if (args['m1'] or args['m2']) and args['r']:
		sys.exit('Cannot use both -1/-2 and -U')
	if (args['m1'] and not args['m2']) or (args['m2'] and not args['m1']):
		sys.exit('Must specify both -1 and -2 for paired-end reads')
	if not (args['m1'] or args['r']):
		sys.exit('Specify reads using either -1 and -2 or -U')
	if args['m1'] and not os.path.isfile(args['m1']):
		sys.exit('Input file specified with -1 does not exist')
	if args['m2'] and not os.path.isfile(args['m2']):
		sys.exit('Input file specified with -2 does not exist')
	if args['r'] and not os.path.isfile(args['r']):
		sys.exit('Input file specified with -U does not exist')
	if args['db_dir'] and not os.path.isdir(args['db_dir']):
		sys.exit('Input directory specified with --db-dir does not exist')

	# Output options
	if not args['out']:
		sys.exit('Specify output directory with -o')

def print_copyright():
	# print out copyright information
	print ("-------------------------------------------------------------------------")
	print ("MicrobeCNV - estimation of gene-copy-number from shotgun sequence data")
	print ("version %s; github.com/snayfach/MicrobeCNV" % __version__)
	print ("Copyright (C) 2015 Stephen Nayfach")
	print ("Freely distributed under the GNU General Public License (GPLv3)")
	print ("-------------------------------------------------------------------------\n")

def parse_profile(inpath):
	""" Parse output from MicrobeSpecies """
	infile = open(inpath)
	next(infile)
	for line in infile:
		fields = [
			('cluster_id', str), ('mapped_reads', int), ('prop_mapped', float),
			('cell_count', float), ('prop_cells', float), ('avg_pid', float)]
		values = line.rstrip().split()
		yield dict( [ (f[0], f[1](v)) for f, v in zip(fields, values)] )

def select_genome_clusters(args):
	""" Select genome clusters to map to """
	cluster_to_abun = {}
	inpath = os.path.join(args['out'], 'species')
	if not os.path.isfile(inpath):
		sys.exit("Could not locate species profile: %s" % inpath)
	for rec in parse_profile(inpath):
		if rec['prop_cells'] >= args['abun']:
			cluster_to_abun[rec['cluster_id']] = rec['prop_cells']
	return cluster_to_abun

def align_reads(genome_clusters):
	""" Use Bowtie2 to map reads to all specified genome clusters """
	for cluster_id in genome_clusters:
	
		# create output directory
		try: os.mkdir(os.path.join(args['out'], 'bam'))
		except: pass
		
		# Build command
		index_bn = '/'.join([args['db_dir'], cluster_id, cluster_id])
		command = 'bowtie2 --no-unal --very-sensitive -x %s ' % index_bn
		#   max reads to search
		if args['reads']: command += '-u %s ' % args['reads']
		#   input files
		if args['m1']: command += '-1 %s -2 %s ' % (args['m1'], args['m2'])
		else: command += '-U %(r)s ' % args['r']
		#   output
		command += '| samtools view -b - > %s' % '/'.join([args['out'], 'bam', '%s.bam' % cluster_id])
		# Run command
		if args['verbose']: print("  running: %s") % command
		process = subprocess.Popen(command, shell=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
		out, err = process.communicate()

def fetch_paired_reads(aln_file):
	""" Use pysam to yield paired end reads from bam file """
	pe_read = []
	for aln in aln_file.fetch(until_eof = True):
		if aln.mate_is_unmapped and aln.is_read1:
			yield [aln]
		elif aln.mate_is_unmapped and aln.is_read2:
			yield [aln]
		else:
			pe_read.append(aln)
			if len(pe_read) == 2:
				yield pe_read
				pe_read = []

def compute_aln_score(pe_read):
	""" Compute alignment score for paired-end read """
	if pe_read[0].mate_is_unmapped:
		score = pe_read[0].query_length - dict(pe_read[0].tags)['NM']
		return score
	else:
		score1 = pe_read[0].query_length - dict(pe_read[0].tags)['NM']
		score2 = pe_read[1].query_length - dict(pe_read[1].tags)['NM']
		return score1 + score2

def compute_perc_id(pe_read):
	""" Compute percent identity for paired-end read """
	if pe_read[0].mate_is_unmapped:
		length = pe_read[0].query_length
		edit = dict(pe_read[0].tags)['NM']
	else:
		length = pe_read[0].query_length + pe_read[1].query_length
		edit = dict(pe_read[0].tags)['NM'] + dict(pe_read[1].tags)['NM']
	return 100 * (length - edit)/float(length)

def find_best_hits(genome_clusters):
	""" Find top scoring alignment for each read """
	if args['verbose']: print("  finding best alignments across GCs:")
	best_hits = {}
	reference_map = {}
	
	# map reads across genome clusters
	for cluster_id in genome_clusters:
		bam_path = '/'.join([args['out'], 'bam', '%s.bam' % cluster_id])
		if not os.path.isfile(bam_path): # check that bam file exists
			sys.stderr.write("    bam file not found for genome-cluster %s. skipping\n" % cluster_id)
			continue
		if args['verbose']: print("     %s") % os.path.basename(bam_path)
		aln_file = pysam.AlignmentFile(bam_path, "rb")
		for pe_read in fetch_paired_reads(aln_file):
			# map reference ids
			for aln in pe_read:
				ref_index = aln.reference_id
				ref_id = aln_file.getrname(ref_index).split('|')[1]
				reference_map[(cluster_id, ref_index)] = ref_id
			# parse pe_read
			query = pe_read[0].query_name
			score = compute_aln_score(pe_read)
			pid = compute_perc_id(pe_read)
			if pid < args['pid']: # filter aln
				continue
			elif query not in best_hits: # store aln
				best_hits[query] = {'score':score, 'aln':{cluster_id:pe_read} }
			elif score > best_hits[query]['score']: # update aln
				best_hits[query] = {'score':score, 'aln':{cluster_id:pe_read} }
			elif score == best_hits[query]['score']: # append aln
				best_hits[query]['aln'][cluster_id] = pe_read
	return best_hits, reference_map

def report_mapping_summary(best_hits):
	""" Summarize hits to genome-clusters """
	hit1, hit2, hit3 = 0, 0, 0
	for value in best_hits.values():
		if len(value['aln']) == 1: hit1 += 1
		elif len(value['aln']) == 2: hit2 += 1
		else: hit3 += 1
	print("  summary:")
	print("    %s reads assigned to any GC (%s)" % (hit1+hit2+hit3, round(float(hit1+hit2+hit3)/args['reads'], 2)) )
	print("    %s reads assigned to 1 GC (%s)" % (hit1, round(float(hit1)/args['reads'], 2)) )
	print("    %s reads assigned to 2 GCs (%s)" % (hit2, round(float(hit2)/args['reads'], 2)) )
	print("    %s reads assigned to 3 or more GCs (%s)" % (hit3, round(float(hit3)/args['reads'], 2)) )

	
def resolve_ties(best_hits, cluster_to_abun):
	""" Reassign reads that map equally well to >1 genome cluster """
	if args['verbose']: print("  reassigning reads mapped to >1 GC")
	for query, rec in best_hits.items():
		if len(rec['aln']) == 1:
			best_hits[query] = rec['aln'].items()[0]
		if len(rec['aln']) > 1:
			target_gcs = rec['aln'].keys()
			abunds = [cluster_to_abun[gc] for gc in target_gcs]
			probs = [abund/sum(abunds) for abund in abunds]
			selected_gc = np.random.choice(target_gcs, 1, p=probs)[0]
			best_hits[query] = (selected_gc, rec['aln'][selected_gc])
	return best_hits

def write_best_hits(selected_clusters, best_hits, reference_map):
	""" Write reassigned PE reads to disk """
	if args['verbose']: print("  writing mapped reads to disk")
	
	# open filehandles
	aln_files = {}
	scaffold_to_genome = {}
	for bam_file in os.listdir('/'.join([args['out'], 'bam'])):
	
		cluster_id = bam_file.split('.')[0]
		try: os.makedirs('/'.join([args['out'], 'reassigned', cluster_id]))
		except: pass
		
		# template bamfile
		inpath = '/'.join([args['out'], 'bam', bam_file])
		if not os.path.isfile(inpath): continue # why am i continuing here...I should make --abun depend on alignment step...or somtehing
		template = pysam.AlignmentFile(inpath, 'rb')
		
		# get genomes from cluster
		infile = gzip.open('/'.join([args['db_dir'], cluster_id, '%s.genome_to_scaffold.gz' % cluster_id]))
		next(infile)
		for line in infile:
		
			# map scaffold to genome
			genome_id, scaffold_id = line.rstrip().split()
			scaffold_to_genome[scaffold_id] = genome_id
			
			# store filehandle
			outpath = '/'.join([args['out'], 'reassigned', cluster_id, '%s.bam' % genome_id])
			aln_files[genome_id] = pysam.AlignmentFile(outpath, 'wb', template=template)

	# write reads to disk
	for cluster_id, pe_read in best_hits.values():
		for aln in pe_read:
			scaffold_id = reference_map[cluster_id, aln.reference_id]
			genome_id = scaffold_to_genome[scaffold_id]
			aln_files[genome_id].write(aln)

def parse_bedfile(inpath):
	""" Parse records from bedfile; start/end coordinates are 1-based """
	fields = [('genome_id', str), ('pangene_id', str), ('type', str),
		      ('gene_id', str), ('scaffold_id', str), ('start', int), ('end', int)]
	infile = gzip.open(inpath)
	next(infile)
	for line in infile:
		values = line.rstrip().split()
		yield dict( [(f[0], f[1](v)) for f, v in zip(fields, values)] )

def map_pangene_locations(coords_to_pangene, my_genome_id):
	""" Parse bedfile and return dictionary mapping a scaffold and position to a set of pangenes """
	pos_to_pangenes = {} # 0-based coordinates
	for genome_id, scaffold_id, start, end in coords_to_pangene:
		if genome_id == my_genome_id:
			pangene_id = coords_to_pangene[genome_id, scaffold_id, start, end]
			for pos in range(start, end+1):
				try: pos_to_pangenes[scaffold_id, pos].add(pangene_id)
				except: pos_to_pangenes[scaffold_id, pos] = set([pangene_id])
	return pos_to_pangenes

def init_pangene_to_bp(coords_to_pangene):
	""" Initiate dictionary mapping a pangenes to its coverage in bp """
	pangene_to_bp = {}
	for pangene_id in coords_to_pangene.values():
		pangene_to_bp[pangene_id] = 0
	return pangene_to_bp

def write_pangene_coverage(pangene_to_cov, cluster_id):
	""" Write coverage of pangenes for genome cluster to disk """
	outdir = '/'.join([args['out'], 'coverage'])
	try: os.mkdir(outdir)
	except: pass
	outfile = gzip.open('/'.join([outdir, '%s.cov.gz' % cluster_id]), 'w')
	for pangene in sorted(pangene_to_cov.keys()):
		cov = str(pangene_to_cov[pangene])
		outfile.write('\t'.join([pangene, cov])+'\n')

def compute_pangenome_coverage(cluster_id):
	""" Count the number of bp mapped to each pangene """
	# read in bedfile using 0-based coordinates; fix start/end coordinates
	coords_to_pangene = {}
	pangene_to_length = {}
	inpath = '/'.join([args['db_dir'], cluster_id, '%s.bed.gz' % cluster_id])
	for r in parse_bedfile(inpath):
		start = min(r['start']-1, r['end']-1)
		end = max(r['start']-1, r['end']-1)
		length = (end - start + 1)
		coords = (r['genome_id'], r['scaffold_id'], start, end)
		pangene = '_'.join([r['pangene_id'], r['type']])
		coords_to_pangene[coords] = pangene
		pangene_to_length[pangene] = length
	# init count of bp per pangene
	pangene_to_bp = init_pangene_to_bp(coords_to_pangene)
	# compute pangene coverage for each genome
	for bam_file in os.listdir('/'.join([args['out'], 'reassigned', cluster_id])):
		# map each genomic position (scaffold, pos) to a pangene id
		genome_id = bam_file.split('.')[0]
		pos_to_pangenes = map_pangene_locations(coords_to_pangene, genome_id)
		# count bp
		inpath = '/'.join([args['out'], 'reassigned', cluster_id, bam_file])
		aln_file = pysam.AlignmentFile(inpath, 'rb')
		for aln in aln_file:
			scaffold_id = aln_file.getrname(aln.reference_id).split('|')[1]
			for pos in range(aln.reference_start, aln.reference_end):
				try:
					pangenes = pos_to_pangenes[scaffold_id, pos]
					for pangene_id in pangenes:
						pangene_to_bp[pangene_id] += 1
				except:
					pass
	# compute coverage
	pangene_to_cov = {}
	for pangene, bp in pangene_to_bp.items():
		cov = float(bp)/pangene_to_length[pangene]
		pangene_to_cov[pangene] = cov
	return pangene_to_cov

# Main
# ------

args = parse_arguments()
check_arguments(args)

if args['verbose']: print_copyright()

if args['profile']:
	if args['verbose']: print("Estimating the abundance of genome-clusters")
	pass
cluster_to_abun = select_genome_clusters(args)
selected_clusters = cluster_to_abun.keys()

if args['align']:
	start = time.time()
	if args['verbose']: print("Aligning reads to reference genomes")
	align_reads(selected_clusters)
	if args['verbose']: print("  %s minutes\n" % round((time.time() - start)/60, 2) )

if args['map']:
	start = time.time()
	if args['verbose']: print("Mapping reads to genome clusters")
	best_hits, reference_map = find_best_hits(selected_clusters)
	if args['verbose']: report_mapping_summary(best_hits)
	best_hits = resolve_ties(best_hits, cluster_to_abun)
	write_best_hits(selected_clusters, best_hits, reference_map)
	if args['verbose']: print("  %s minutes\n" % round((time.time() - start)/60, 2) )

if args['cov']:
	start = time.time()
	if args['verbose']: print("Computing coverage of pangenomes")
	for cluster_id in os.listdir('/'.join([args['out'], 'reassigned'])):
		pangene_to_cov = compute_pangenome_coverage(cluster_id)
		write_pangene_coverage(pangene_to_cov, cluster_id)
	if args['verbose']: print("  %s minutes\n" % round((time.time() - start)/60, 2) )


