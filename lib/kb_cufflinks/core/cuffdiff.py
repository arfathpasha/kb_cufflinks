import os
import uuid
from pprint import pprint
import errno
import zipfile
import shutil
import multiprocessing as mp
import handler_utils
import script_utils
from cuffmerge import CuffMerge

from Workspace.WorkspaceClient import Workspace as Workspace
from DataFileUtil.DataFileUtilClient import DataFileUtil
from GenomeFileUtil.GenomeFileUtilClient import GenomeFileUtil
from KBaseReport.KBaseReportClient import KBaseReport

class CuffDiff:

    GFFREAD_TOOLKIT_PATH = '/kb/deployment/bin/gffread'

    def _process_params(self, params):
        """
        validates params passed to run_CuffDiff method
        """
        for p in ['expressionset_ref',
                  'workspace_name',
                  'diff_expression_obj_name'
                  ]:
            if p not in params:
                raise ValueError('"{}" parameter is required, but missing'.format(p))


    def _run_gffread(self, gff_path, gtf_path, result_dir):
        """
        _run_gffread: run gffread script
        ref: http://ccb.jhu.edu/software/stringtie/gff.shtml
        """
        print('converting gff to gtf')
        command = self.GFFREAD_TOOLKIT_PATH + '/gffread '
        command += "-E {0} -T -o {1}".format(gff_path, gtf_path)

        ret = script_utils.runProgram(self.logger, "gffread", command)

    def _create_gtf_file(self, genome_ref):
        """
        _create_gtf_file: create reference annotation file from genome
        """
        result_dir = self.scratch

        genome_gff_file = self.gfu.genome_to_gff({'genome_ref': genome_ref,
                                                  'target_dir': result_dir})['file_path']

        gtf_ext = '.gtf'
        if not genome_gff_file.endswith(gtf_ext):
            gtf_path = os.path.splitext(genome_gff_file)[0] + '.gtf'
            self._run_gffread(genome_gff_file, gtf_path, result_dir)
        else:
            gtf_path = genome_gff_file

        return gtf_path

    def _get_gtf_file(self, genome_ref, result_dir):
        """
        _get_gtf_file: get the reference annotation file (in GTF or GFF3 format)
        """
        genome_data = self.ws_client.get_objects2({'objects':
                                            [{'ref': genome_ref}]})['data'][0]['data']

        gff_handle_ref = genome_data.get('gff_handle_ref')

        if gff_handle_ref:
            annotation_file = self.dfu.shock_to_file({'handle_id': gff_handle_ref,
                                                      'file_path': result_dir,
                                                      'unpack': 'unpack'})['file_path']
        else:
            annotation_file = self._create_gtf_file(genome_ref)
        return annotation_file

    def _generate_output_file_list(self, result_directory):
        """
        _generate_output_file_list: zip result files and generate file_links for report
        """
        self.logger.info('Start packing result files')
        output_files = list()

        output_directory = os.path.join(self.scratch, 'outfile_' + str(uuid.uuid4()))
        handler_utils._mkdir_p(output_directory)
        result_file = os.path.join(output_directory, 'cuffdiff_result.zip')
        plot_file = os.path.join(output_directory, 'cuffdiff_plot.zip')

        with zipfile.ZipFile(result_file, 'w',
                             zipfile.ZIP_DEFLATED,
                             allowZip64=True) as zip_file:
            for root, dirs, files in os.walk(result_directory):
                for file in files:
                    if not (file.endswith('.zip') or
                                file.endswith('.png') or
                                file.endswith('.DS_Store')):
                        zip_file.write(os.path.join(root, file), file)

        output_files.append({'path': result_file,
                             'name': os.path.basename(result_file),
                             'label': os.path.basename(result_file),
                             'description': 'File(s) generated by Cuffdiff App'})

        with zipfile.ZipFile(plot_file, 'w',
                             zipfile.ZIP_DEFLATED,
                             allowZip64=True) as zip_file:
            for root, dirs, files in os.walk(result_directory):
                for file in files:
                    if file.endswith('.png'):
                        zip_file.write(os.path.join(root, file), file)

        output_files.append({'path': plot_file,
                             'name': os.path.basename(plot_file),
                             'label': os.path.basename(plot_file),
                             'description': 'Visualization plots by Cuffdiff App'})

        return output_files

    def _generate_html_report(self, result_directory, diff_expression_obj_ref,
                              params):
        """
        _generate_html_report: generate html summary report
        """

        self.logger.info('Start generating html report')
        html_report = list()

        output_directory = os.path.join(self.scratch, str(uuid.uuid4()))
        handler_utils._mkdir_p(output_directory)
        result_file_path = os.path.join(output_directory, 'report.html')

        shutil.copy2(os.path.join(result_directory, 'gene_exp.diff'),
                     os.path.join(output_directory, 'gene_exp.diff'))
        shutil.copy2(os.path.join(result_directory, 'isoform_exp.diff'),
                     os.path.join(output_directory, 'isoform_exp.diff'))
        shutil.copy2(os.path.join(result_directory, 'promoters.diff'),
                     os.path.join(output_directory, 'promoters.diff'))
        shutil.copy2(os.path.join(result_directory, 'splicing.diff'),
                     os.path.join(output_directory, 'splicing.diff'))

        overview_content = ''
        overview_content += '<p>Generated Differential Expression Object:</p><p>{}</p>'.format(
                                                    params.get('diff_expression_obj_name'))

        with open(result_file_path, 'w') as result_file:
            with open(os.path.join(os.path.dirname(__file__), 'report_template.html'),
                      'r') as report_template_file:
                report_template = report_template_file.read()
                report_template = report_template.replace('<p>Overview_Content</p>',
                                                          overview_content)
                result_file.write(report_template)

        report_shock_id = self.dfu.file_to_shock({'file_path': output_directory,
                                                  'pack': 'zip'})['shock_id']

        html_report.append({'shock_id': report_shock_id,
                            'name': os.path.basename(result_file_path),
                            'label': os.path.basename(result_file_path),
                            'description': 'HTML summary report for Cuffdiff App'})
        return html_report

    def _generate_report(self, diff_expression_obj_ref,
                         params, result_directory):
        """
        _generate_report: generate summary report
        """
        self.logger.info('Creating report')

        output_files = self._generate_output_file_list(result_directory)

        output_html_files = self._generate_html_report(result_directory,
                                                       diff_expression_obj_ref,
                                                       params)
        report_params = {
                         'message': '',
                         'workspace_name': params.get('workspace_name'),
                         'file_links': output_files,
                         'html_links': output_html_files,
                         'direct_html_link_index': 0,
                         'html_window_height': 333,
                         'report_object_name': 'kb_cuffdiff_report_' + str(uuid.uuid4())
                        }

        kbase_report_client = KBaseReport(self.callback_url)
        output = kbase_report_client.create_extended_report(report_params)

        report_output = {'report_name': output['name'], 'report_ref': output['ref']}

        return report_output

    def _gen_diff_expression_data(self, expressionset_data, result_directory):
        """
        _generate_diff_expression_data: generate RNASeqDifferentialExpression object data
        """
        diff_expression_data = {
                                'tool_used': 'cuffdiff',
                                'tool_version': '2.2.1',
                                'expressionSet_id': expressionset_data.get('expressionSet_id'),
                                'genome_id': expressionset_data.get('genome_id'),
                                'alignmentSet_id': expressionset_data.get('alignmentSet_id'),
                                'sampleset_id': expressionset_data.get('sampleset_id')
                                }

        #self._generate_diff_expression_csv(result_directory, alpha_cutoff,
                                           #fold_change_cutoff, condition_string)

        handle = self.dfu.file_to_shock({'file_path': result_directory,
                                         'pack': 'zip',
                                         'make_handle': True})['handle']
        diff_expression_data.update({'file': handle})
        diff_expression_data.update({'condition': expressionset_data.get('condition')})

        return diff_expression_data

    def _save_diff_expression(self, params, diff_exp_data):

        workspace_name = params.get('workspace_name')
        output_name = params['diff_expression_obj_name']

        if isinstance(workspace_name, int) or workspace_name.isdigit():
            workspace_id = workspace_name
        else:
            workspace_id = self.dfu.ws_name_to_id(workspace_name)

        dfu_oi = self.ws_client.save_objects({'id': workspace_id,
                                            "objects": [{
                                            "type": "KBaseRNASeq.RNASeqDifferentialExpression",
                                            "data": diff_exp_data,
                                            "name": output_name
                                            }]
                                            })[0]
        diff_expression_obj_ref = str(dfu_oi[6]) + '/' + str(dfu_oi[0]) + '/' + str(dfu_oi[4])
        return diff_expression_obj_ref

    def _get_expressionset_data(self, expressionset_ref, result_directory):
        """
        Get data from expressionset object in the form required for input to
        cuffmerge and cuffdiff
        """
        expression_set = self.ws_client.get_objects2(
            {'objects':
                 [{'ref': expressionset_ref}]})['data'][0]

        expression_set_data = expression_set['data']

        output_data = {}
        output_data['expressionSet_id'] = expressionset_ref
        output_data['alignmentSet_id'] = expression_set_data.get('alignmentSet_id')
        output_data['sampleset_id'] = expression_set_data.get('sampleset_id')
        output_data['genome_id'] = expression_set_data.get('genome_id')

        """
        Get gtf file from genome_ref. Used as input to cuffmerge.
        """
        output_data['gtf_file_path'] = self._get_gtf_file(output_data['genome_id'], result_directory)

        condition = []
        bam_files = []

        mapped_expr_ids = expression_set_data.get('mapped_expression_ids')
        """
        assembly_gtf.txt will contain the file paths of all .gtf files in the expressionset.
        Used as input to cuffmerge.
        """
        assembly_file = os.path.join(result_directory, "assembly_gtf.txt")
        list_file = open(assembly_file, 'w')
        for i in mapped_expr_ids:
            for alignment_id, expression_id in i.items():
                expression_data = self.ws_client.get_objects2(
                    {'objects':
                         [{'ref': expression_id}]})['data'][0]['data']

                handle_id = expression_data.get('file').get('hid')
                expression_name = os.path.splitext(expression_data.get('file').get('file_name'))[0]
                tmp_gtf_directory = os.path.join(result_directory, expression_name)
                handler_utils._mkdir_p(tmp_gtf_directory)

                self.dfu.shock_to_file({'handle_id': handle_id,
                                        'file_path': tmp_gtf_directory,
                                        'unpack': 'unpack'})

                e_file_path = os.path.join(tmp_gtf_directory, "transcripts.gtf")
                if os.path.exists(e_file_path):
                    print e_file_path
                    print('Adding: ' + e_file_path)
                    list_file.write("{0}\n".format(e_file_path))
                else:
                    raise ValueError(e_file_path + " not found")
                """
                List of bam files in alignment set. Used as input to cuffdiff.
                """
                alignment_data = self.ws_client.get_objects2(
                    {'objects':
                         [{'ref': alignment_id}]})['data'][0]['data']

                handle_id = alignment_data.get('file').get('hid')
                alignment_name, ext = os.path.splitext(alignment_data.get('file').get('file_name'))
                tmp_bam_directory = os.path.join(result_directory, alignment_name)
                handler_utils._mkdir_p(tmp_bam_directory)

                self.dfu.shock_to_file({'handle_id': handle_id,
                                        'file_path': tmp_bam_directory,
                                        'unpack': 'unpack'})

                a_file_path = os.path.join(tmp_bam_directory, "accepted_hits.bam")
                if os.path.exists(a_file_path):
                    print a_file_path
                    bam_files.append(a_file_path)
                else:
                    raise ValueError(a_file_path + " not found")

                """
                List of all conditions in expressionset. Used as input to cuffdiff.
                """
                condition.append(expression_data.get('condition'))

        list_file.close()
        output_data['assembly_file'] = assembly_file
        output_data['condition'] = condition
        output_data['bam_files'] = bam_files
        return output_data

    def _assemble_cuffdiff_command(self, params, expressionset_data, merged_gtf, output_dir):

        bam_files = " ".join(expressionset_data.get('bam_files'))
        t_labels = ",".join(expressionset_data.get('condition'))

        # output_dir = os.path.join(cuffdiff_dir, self.method_params['output_obj_name'])

        cuffdiff_command = (' -p ' + str(self.num_threads))
        """
        Set Advanced parameters for Cuffdiff
        """
        if ('time_series' in params and params['time_series'] != 0):
            cuffdiff_command += (' -T ')
        if ('min_alignment_count' in params and
                    params['min_alignment_count'] is not None):
            cuffdiff_command += (' -c ' + str(params['min_alignment_count']))
        if ('multi_read_correct' in params and
                        params['multi_read_correct'] != 0):
            cuffdiff_command += (' --multi-read-correct ')
        if ('library_type' in params and
                    params['library_type'] is not None):
            cuffdiff_command += (' --library-type ' + params['library_type'])
        if ('library_norm_method' in params and
                        params['library_norm_method'] is not None):
            cuffdiff_command += (' --library-norm-method ' + params['library_norm_method'])

        cuffdiff_command += " -o {0} -L {1} -u {2} {3}".format(output_dir,
                                                               t_labels,
                                                               merged_gtf,
                                                               bam_files)
        return cuffdiff_command

    def __init__(self, config, services, logger=None):
        self.config = config
        self.logger = logger
        self.callback_url = os.environ['SDK_CALLBACK_URL']
        self.scratch = os.path.join(config['scratch'], 'cuffdiff_merge_' + str(uuid.uuid4()))
        self.ws_url = config['workspace-url']
        self.services = services
        self.ws_client = Workspace(self.services['workspace_service_url'])
        self.dfu = DataFileUtil(self.callback_url)
        self.gfu = GenomeFileUtil(self.callback_url)
        self.cuffmerge_runner = CuffMerge(config, logger)
        self.num_threads = mp.cpu_count()
        handler_utils._mkdir_p(self.scratch)

    def run_cuffdiff(self, params):
        """
        Check input parameters
        """
        self._process_params(params)

        expressionset_ref = params.get('expressionset_ref')
        result_directory = os.path.join(self.scratch, 'expset_' + str(uuid.uuid4()))
        handler_utils._mkdir_p(result_directory)

        """
        Get data from expressionset in a format needed for cuffmerge and cuffdiff
        """
        expressionset_data = self._get_expressionset_data(expressionset_ref, result_directory)

        """
        Run cuffmerge
        """
        cuffmerge_dir = os.path.join(self.scratch, "cuffmerge_" + str(uuid.uuid4()))
        merged_gtf = self.cuffmerge_runner.run_cuffmerge(cuffmerge_dir,
                                                        self.num_threads,
                                                        expressionset_data.get('gtf_file_path'),
                                                        expressionset_data.get('assembly_file'))
        self.logger.info('MERGED GTF FILE: ' + merged_gtf)

        """
        Assemble parameters and run cuffdiff
        """
        cuffdiff_dir = os.path.join(self.scratch, "cuffdiff_" + str(uuid.uuid4()))
        handler_utils._mkdir_p(cuffdiff_dir)

        cuffdiff_command = self._assemble_cuffdiff_command(params,
                                                           expressionset_data,
                                                           merged_gtf,
                                                           cuffdiff_dir)
        try:
            ret = script_utils.runProgram(self.logger,
                                          "cuffdiff",
                                          cuffdiff_command,
                                          None,
                                          cuffdiff_dir)
            result = ret["result"]
            for line in result.splitlines(False):
                self.logger.info(line)
                stderr = ret["stderr"]
                prev_value = ''
                for line in stderr.splitlines(False):
                    if line.startswith('> Processing Locus'):
                        words = line.split()
                        cur_value = words[len(words) - 1]
                        if prev_value != cur_value:
                            prev_value = cur_value
                            self.logger.info(line)
                        else:
                            prev_value = ''
                            self.logger.info(line)
        except Exception, e:
            raise Exception("Error executing cuffdiff {0},{1}".format(cuffdiff_command, e))

        """
        Save differential expression object
        """
        diff_exp_data = self._gen_diff_expression_data(expressionset_data, cuffdiff_dir)

        diffexp_obj_ref = self._save_diff_expression(params, diff_exp_data)

        returnVal = {'result_directory': cuffdiff_dir,
                     'diff_expression_obj_ref': diffexp_obj_ref
                     }

        report_output = self._generate_report(diffexp_obj_ref,
                                              params,
                                              cuffdiff_dir)
        returnVal.update(report_output)

        return returnVal
